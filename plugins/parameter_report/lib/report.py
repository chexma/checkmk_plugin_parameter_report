"""Build the report: render host chunks in parallel and merge them."""

from __future__ import annotations

import ctypes
import multiprocessing
import os
import shutil
import signal
import tempfile
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject
from reportlab.pdfbase.pdfmetrics import stringWidth

from . import render, render_canvas, toc
from .i18n import text
from .model import HostReport, ReportSummary
from .runstate import SITE_MARKER, TEMP_PREFIX, peak_rss_mb
from .settings import Settings

T = TypeVar("T")
PR_SET_PDEATHSIG = 1
Progress = Callable[[int, int], None]


class ReportError(Exception):
    """The report could not be built; the message is shown to the user."""


@dataclass(frozen=True)
class BuildResult:
    timings: dict[str, float]
    worker_peak_mb: int  # largest render process (the main process with one worker)


def chunked(items: Sequence[T], size: int) -> list[list[T]]:
    if size < 1:
        raise ValueError("chunk size must be at least 1")
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def worker_init() -> None:
    """Initializer of every spawned helper process (render workers, collector)."""
    try:
        # Die with the parent: a killed report must not leave helpers behind
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
    except (OSError, AttributeError):
        pass  # not Linux/glibc: helpers may outlive a killed parent
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl-C is handled by the parent


def terminate(pool: ProcessPoolExecutor) -> None:
    """Stop the pool's processes now; shutdown() alone would wait for them."""
    for process in list(getattr(pool, "_processes", {}).values()):  # public API from 3.14
        process.terminate()
    pool.shutdown(wait=True, cancel_futures=True)


def _render_chunk(
    hosts: list[HostReport],
    path: Path,
    generated_at: datetime,
    settings: Settings,
) -> int:
    render_canvas.render_hosts(hosts, path, generated_at, settings)
    return peak_rss_mb()


def _render_chunks(
    chunks: list[list[HostReport]],
    paths: list[Path],
    generated_at: datetime,
    settings: Settings,
    progress: Progress,
) -> int:
    """Render all chunks; returns the peak RSS (MB) of the largest render process."""
    total = sum(len(chunk) for chunk in chunks)
    done = peak = 0
    if settings.workers <= 1:
        for chunk, path in zip(chunks, paths):
            peak = max(peak, _render_chunk(chunk, path, generated_at, settings))
            done += len(chunk)
            progress(done, total)
        return peak
    # spawn: workers must not inherit the parent's state
    pool = ProcessPoolExecutor(
        max_workers=min(settings.workers, len(chunks)),
        mp_context=multiprocessing.get_context("spawn"),
        initializer=worker_init,
    )
    try:
        futures = {
            pool.submit(_render_chunk, chunk, path, generated_at, settings): len(chunk)
            for chunk, path in zip(chunks, paths)
        }
        for future in as_completed(futures):
            peak = max(peak, future.result())
            done += futures[future]
            progress(done, total)
    except BaseException:
        terminate(pool)
        raise
    pool.shutdown(wait=True)
    return peak


def _contents(
    cover: Path,
    chunks: Sequence[Path],
    hosts: int,
    summary: ReportSummary,
    target: Path,
    settings: Settings,
    appendix: bool,
) -> list[toc.PageLinks]:
    """Render the table of contents with the final page numbers of all hosts."""
    failed = {host for host, _error in summary.failed_hosts}
    page = len(PdfReader(cover).pages) + toc.page_count(
        hosts, settings, appendix=1 if appendix else 0
    )
    entries = []
    for path in chunks:  # one reader at a time: the merge loads all chunks again
        reader = PdfReader(path)
        for item in reader.outline:  # one top-level bookmark per host, in host order
            start = page + reader.get_destination_page_number(item)
            entries.append(toc.Entry(item.title, start + 1, item.title in failed))
        page += len(reader.pages)
    if len(entries) != hosts:  # a renderer no longer writes one bookmark per host
        raise ReportError(f"Table of contents: {len(entries)} bookmarks for {hosts} hosts")
    if appendix:  # page counts all pages before the appendix
        entries.append(toc.Entry(text(settings.language, "time_periods"), page + 1, appendix=True))
    return toc.render(entries, target, settings)


PAGE_NUMBER_SIZE = 7  # pt, like the other footer texts


def _stamp_page_numbers(writer: PdfWriter, settings: Settings) -> None:
    """Report-wide "page N of TOTAL" in every footer, matching the table of contents.

    Each page gets one tiny extra content stream, fenced by q/Q from the page's
    own content; that content stays as rendered (compressed). merge_page() would
    rewrite every page uncompressed: 10x the file size for 1,000 hosts.
    """
    total = len(writer.pages)
    width = settings.page[0] - settings.margin
    # pypdf has no public call to add an object; _add_object is what its docs use
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/" + toc.FONT),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )
    save = writer._add_object(_stream(b"q\n"))
    for number, page in enumerate(writer.pages, 1):
        label = text(settings.language, "page_of_total", page=number, total=total)
        x = width - stringWidth(label, toc.FONT, PAGE_NUMBER_SIZE)
        stamp = f"\nQ q BT /PRPageNumber {PAGE_NUMBER_SIZE} Tf 0 g {x:.2f} {toc.FOOTER_Y:.2f} Td "
        literal = label.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stamp += f"({literal}) Tj ET Q\n"
        contents = page.get("/Contents")
        own = list(contents.get_object()) if isinstance(contents.get_object(), list) else [contents]
        page[NameObject("/Contents")] = ArrayObject(
            [save, *own, writer._add_object(_stream(stamp.encode("cp1252", "replace")))]
        )
        resources = page[NameObject("/Resources")].get_object()
        if "/Font" not in resources:
            resources[NameObject("/Font")] = DictionaryObject()
        resources["/Font"].get_object()[NameObject("/PRPageNumber")] = font


def _stream(data: bytes) -> DecodedStreamObject:
    stream = DecodedStreamObject()
    stream.set_data(data)
    return stream


def _merge(
    cover: Path,
    chunks: Sequence[Path],
    target: Path,
    settings: Settings,
    contents: Path | None = None,
    links: Sequence[toc.PageLinks] = (),
    appendix: Path | None = None,
) -> None:
    writer = PdfWriter()
    writer.append(cover)
    first_contents_page = len(writer.pages)
    if contents is not None:
        writer.append(contents, outline_item=text(settings.language, "contents"))
    for part in chunks:
        writer.append(part)  # keeps the bookmarks of each part
    if appendix is not None:
        writer.append(appendix, outline_item=text(settings.language, "time_periods"))
    for offset, page_links in enumerate(links):
        for rect, target_page in page_links:
            link = writer.add_annotation(
                first_contents_page + offset, Link(rect=rect, target_page_index=target_page)
            )
            # pypdf writes the page index; an internal link needs the page object itself
            link["/Dest"][0] = writer.pages[target_page].indirect_reference
    _stamp_page_numbers(writer, settings)
    metadata = PdfReader(cover).metadata  # the cover carries the report title
    if metadata:
        writer.add_metadata(metadata)
    with target.open("wb") as fh:
        writer.write(fh)


def build_report(
    hosts: Sequence[HostReport],
    summary: ReportSummary,
    output: Path,
    settings: Settings,
    *,
    progress: Progress | None = None,
) -> BuildResult:
    if not hosts:
        raise ReportError("No hosts for the report")
    progress = progress or (lambda done, total: None)
    output = Path(output)
    chunks = chunked(sorted(hosts, key=lambda h: h.name), settings.chunk_size)
    # Temp dir next to the output, so the final rename is atomic
    tmpdir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=output.parent))
    timings: dict[str, float] = {}
    try:
        (tmpdir / SITE_MARKER).write_text(summary.site)
        start = time.perf_counter()
        paths = [tmpdir / f"chunk-{i:05d}.pdf" for i in range(len(chunks))]
        cover = tmpdir / "cover.pdf"
        try:
            worker_peak = _render_chunks(chunks, paths, summary.generated_at, settings, progress)
            render.render_cover(summary, cover, settings)
            appendix = None
            if summary.timeperiods:
                appendix = tmpdir / "timeperiods.pdf"
                render.render_timeperiods(summary.timeperiods, appendix, settings)
        except Exception as exc:
            raise ReportError(f"Rendering failed: {exc}") from exc
        timings["render"] = time.perf_counter() - start

        start = time.perf_counter()
        merged = tmpdir / "report.pdf"
        try:
            contents, links = None, []
            if settings.toc:
                contents = tmpdir / "contents.pdf"
                links = _contents(
                    cover, paths, len(hosts), summary, contents, settings, appendix is not None
                )
            _merge(cover, paths, merged, settings, contents, links, appendix)
            os.replace(merged, output)
        except Exception as exc:
            raise ReportError(f"Merging failed: {exc}") from exc
        timings["merge"] = time.perf_counter() - start
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return BuildResult(timings, worker_peak)
