from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from app.config import Settings
from app.schemas import DOCUMENT_TYPES, DocumentType, DownloadResult, MatterMetadata
from app.zip_service import slugify

logger = logging.getLogger(__name__)


class ScrapeError(RuntimeError):
    pass


class UARBScraper:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def scrape_metadata_and_counts(
        self,
        matter_number: str,
    ) -> tuple[MatterMetadata, dict[str, int]]:
        logger.info("Scrape start for matter %s", matter_number)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.settings.headless)
            context = browser.new_context(
                accept_downloads=True,
                viewport={"width": 1600, "height": 1200},
            )
            page = context.new_page()
            try:
                page.set_default_timeout(self.settings.scrape_timeout_ms)
                self._open_matter(page, matter_number)
                text = _page_text(page)
                metadata = _extract_metadata(text, matter_number)
                counts = _extract_document_counts(text)
                logger.info("Metadata extracted for %s: %s", matter_number, metadata.model_dump())
                logger.info("Document counts for %s: %s", matter_number, counts)
                return metadata, counts
            except Exception as exc:
                self._save_error_screenshot(page, matter_number, "scrape")
                raise ScrapeError(str(exc)) from exc
            finally:
                context.close()
                browser.close()

    def download_documents(
        self,
        matter_number: str,
        document_type: DocumentType,
        known_counts: dict[str, int] | None = None,
    ) -> DownloadResult:
        logger.info("Download start for %s %s", matter_number, document_type)
        target_dir = self.settings.download_root / matter_number / slugify(document_type)
        target_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.settings.headless)
            context = browser.new_context(
                accept_downloads=True,
                viewport={"width": 1600, "height": 1200},
            )
            page = context.new_page()
            downloaded_files: list[str] = []
            failed_files: list[str] = []
            try:
                page.set_default_timeout(self.settings.scrape_timeout_ms)
                self._open_matter(page, matter_number)
                self._select_document_tab(page, document_type)

                text = _page_text(page)
                counts = _extract_document_counts(text)
                if known_counts:
                    counts = {**known_counts, **counts}
                total_available = counts.get(document_type, _extract_found_count(text))
                if total_available == 0:
                    return DownloadResult(
                        requested_type=document_type,
                        total_available=0,
                        downloaded_count=0,
                        downloaded_files=[],
                        failed_files=[],
                    )

                targets = _collect_visible_document_targets(
                    page,
                    min(self.settings.max_downloads, total_available),
                )
                if not targets:
                    raise ScrapeError("Could not identify visible document rows to download")

                logger.info(
                    "Selected document numbers in site order: %s",
                    [target["doc_no"] for target in targets],
                )
                try:
                    _click_preview_for_doc_no(page, targets[0]["doc_no"])
                    page.get_by_text("Back to List").first.wait_for(timeout=self.settings.scrape_timeout_ms)
                except Exception as exc:
                    self._save_error_screenshot(page, matter_number, "download")
                    raise ScrapeError(f"Could not open the first selected document: {exc}") from exc

                for index, target in enumerate(targets):
                    doc_no = target["doc_no"]
                    try:
                        _wait_for_preview_file(page, self.settings.scrape_timeout_ms)
                        save_path = _download_preview_file(
                            page,
                            target_dir,
                            _filename_for_target(index, target),
                        )
                        downloaded_files.append(str(save_path))
                        logger.info("Downloaded %s", save_path)
                    except Exception as exc:
                        logger.warning("Download failed for doc %s: %s", doc_no, exc)
                        failed_files.append(doc_no)

                    if index < len(targets) - 1:
                        try:
                            current_href = _current_streaming_href(page)
                            _go_to_next_preview(page, current_href, self.settings.scrape_timeout_ms)
                        except Exception as exc:
                            logger.warning("Could not advance from doc %s: %s", doc_no, exc)
                            failed_files.extend(target["doc_no"] for target in targets[index + 1 :])
                            break

                _return_to_list_if_needed(page, self.settings.scrape_timeout_ms)

                return DownloadResult(
                    requested_type=document_type,
                    total_available=total_available,
                    downloaded_count=len(downloaded_files),
                    downloaded_files=downloaded_files,
                    failed_files=failed_files,
                )
            except Exception as exc:
                self._save_error_screenshot(page, matter_number, "download")
                raise ScrapeError(str(exc)) from exc
            finally:
                context.close()
                browser.close()

    def _open_matter(self, page: Page, matter_number: str) -> None:
        page.goto(self.settings.uarb_url, wait_until="domcontentloaded")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(5000)
        self._fill_matter_input(page, matter_number)
        self._click_search(page)
        page.get_by_text("Back to Search Results").first.wait_for(timeout=self.settings.scrape_timeout_ms)
        result_text = _page_text(page)
        if matter_number not in result_text:
            raise ScrapeError(f"Matter page loaded, but {matter_number} was not found")

    def _fill_matter_input(self, page: Page, matter_number: str) -> None:
        candidate_selectors = [
            "input[placeholder*='M01234']",
            "input[placeholder*='m01234' i]",
            "input[type='text']",
        ]
        for selector in candidate_selectors:
            locator = page.locator(selector).first
            try:
                locator.wait_for(timeout=10000)
                locator.fill(matter_number)
                return
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue

        field = _direct_matter_field(page)
        if field is not None:
            editable = field.locator(".text").first
            try:
                editable.click(timeout=10000)
                page.wait_for_timeout(500)
                for character in matter_number:
                    page.keyboard.press(character)
                    page.wait_for_timeout(100)
                page.wait_for_timeout(500)
                if matter_number in _page_text(page):
                    return
            except Exception:
                logger.exception("Unable to type into FileMaker matter field")

        raise ScrapeError("Could not find the Go Directly to Matter input")

    def _click_search(self, page: Page) -> None:
        clicked = _click_direct_matter_search(page)
        if clicked:
            return

        candidates = [
            page.get_by_role("button", name=re.compile(r"^Search$", re.IGNORECASE)).first,
            page.get_by_text(re.compile(r"^Search$", re.IGNORECASE)).first,
        ]
        for locator in candidates:
            try:
                locator.click(timeout=10000)
                return
            except Exception:
                continue
        raise ScrapeError("Could not find the Search button")

    def _select_document_tab(self, page: Page, document_type: str) -> None:
        pattern = re.compile(rf"^{re.escape(document_type)}\s*-\s*\d+", re.IGNORECASE)
        candidates = [
            page.get_by_text(pattern).first,
            page.locator(f"text=/{document_type}/i").first,
        ]
        for locator in candidates:
            try:
                locator.click(timeout=10000)
                page.wait_for_timeout(1000)
                return
            except Exception:
                continue
        raise ScrapeError(f"Could not find tab for {document_type}")

    def _save_error_screenshot(self, page: Page, matter_number: str, phase: str) -> None:
        try:
            error_dir = self.settings.download_root / "errors"
            error_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            path = error_dir / f"{matter_number}_{phase}_{timestamp}.png"
            page.screenshot(path=path, full_page=True)
            logger.info("Saved error screenshot to %s", path)
        except Exception:
            logger.exception("Unable to save error screenshot")


def _direct_matter_field(page: Page):
    candidates = [
        page.locator(".fm-textarea").filter(has_text=re.compile(r"M01234|M\d{5}", re.IGNORECASE)).first,
        page.locator(".fm-textarea-prompt").filter(has_text=re.compile(r"M01234", re.IGNORECASE)).first,
        page.locator(".fm_object_254").first,
    ]
    for locator in candidates:
        try:
            locator.wait_for(timeout=3000)
            return locator
        except Exception:
            continue

    fields = page.locator(".fm-textarea")
    for index in range(fields.count()):
        field = fields.nth(index)
        box = field.bounding_box()
        if box and box["y"] < 330 and box["width"] < 300:
            return field
    return None


def _click_direct_matter_search(page: Page) -> bool:
    field = _direct_matter_field(page)
    if field is None:
        return False

    field_box = field.bounding_box()
    if field_box is None:
        return False

    buttons = page.locator("button").filter(has_text=re.compile(r"^Search$", re.IGNORECASE))
    best_index = None
    best_score = float("inf")
    for index in range(buttons.count()):
        button = buttons.nth(index)
        box = button.bounding_box()
        if box is None:
            continue
        y_delta = abs(box["y"] - field_box["y"])
        x_penalty = 0 if box["x"] > field_box["x"] else 1000
        score = y_delta + x_penalty
        if score < best_score:
            best_index = index
            best_score = score

    if best_index is None:
        return False

    buttons.nth(best_index).click(timeout=10000)
    return True


def _collect_visible_document_targets(page: Page, limit: int) -> list[dict[str, str]]:
    rows = page.locator("tr.v-grid-row-has-data")
    targets: list[dict[str, str]] = []
    seen: set[str] = set()

    for index in range(rows.count()):
        row = rows.nth(index)
        text = row.inner_text(timeout=10000)
        doc_no = _extract_doc_no_from_row(text)
        if doc_no is None or doc_no in seen:
            continue
        seen.add(doc_no)
        targets.append(
            {
                "doc_no": doc_no,
                "title": _extract_title_from_row(text),
            }
        )
        if len(targets) >= limit:
            break

    return targets


def _click_preview_for_doc_no(page: Page, doc_no: str) -> None:
    row = page.locator("tr.v-grid-row-has-data").filter(has_text=re.compile(rf"\b{re.escape(doc_no)}\b")).first
    row.wait_for(timeout=10000)
    row.locator("button").filter(has_text=re.compile("Preview", re.IGNORECASE)).first.click(
        timeout=10000,
        force=True,
    )


def _wait_for_preview_file(
    page: Page,
    timeout_ms: int,
    previous_href: str | None = None,
) -> str:
    page.wait_for_function(
        """
        previous => {
          const file = document.querySelector("a[href*='/Streaming/'], object[data*='/Streaming/']");
          const href = file && (file.getAttribute("href") || file.getAttribute("data"));
          return href && href !== previous;
        }
        """,
        arg=previous_href,
        timeout=timeout_ms,
    )
    href = _current_streaming_href(page)
    if href is None:
        raise ScrapeError("Could not find a streaming file URL on the preview page")
    return href


def _go_to_next_preview(page: Page, current_href: str | None, timeout_ms: int) -> None:
    page.locator("button").filter(has_text=re.compile(r"^Next$", re.IGNORECASE)).first.click(
        timeout=10000,
        force=True,
    )
    _wait_for_preview_file(page, timeout_ms, previous_href=current_href)


def _extract_doc_no_from_row(text: str) -> str | None:
    match = re.search(r"^\s*(\d{4,7})\b", text)
    if match:
        return match.group(1)
    return None


def _extract_title_from_row(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2:
        return lines[1]
    return "document"


def _filename_for_target(index: int, target: dict[str, str]) -> str:
    order = f"{index + 1:02d}"
    doc_no = target["doc_no"]
    title = target.get("title") or "document"
    return f"{order}_{doc_no}_{title}.pdf"


def _download_preview_file(page: Page, target_dir: Path, fallback_name: str) -> Path:
    href = _current_streaming_href(page)
    filename = fallback_name

    if href is None:
        raise ScrapeError("Could not find a streaming file URL on the preview page")

    save_path = _unique_path(target_dir / _safe_filename(filename))
    save_path.write_bytes(_download_url(page, href))
    return save_path


def _download_url(page: Page, href: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = page.context.request.get(href, timeout=90000)
            if not response.ok:
                raise ScrapeError(f"File request failed with HTTP {response.status}")
            return response.body()
        except Exception as exc:
            last_error = exc
            logger.info("PDF download attempt %s failed: %s", attempt, exc)
            time.sleep(1)

    raise ScrapeError(f"File request failed after retries: {last_error}")


def _current_streaming_href(page: Page) -> str | None:
    link = page.locator("a[href*='/Streaming/']").first
    if link.count() > 0:
        href = link.get_attribute("href")
        if href:
            return href

    embedded = page.locator("object[data*='/Streaming/']").first
    if embedded.count() > 0:
        href = embedded.get_attribute("data")
        if href:
            return href

    return None


def _return_to_list_if_needed(page: Page, timeout_ms: int) -> None:
    if page.get_by_text("Back to List").count() == 0:
        return
    try:
        page.get_by_text("Back to List").first.click(timeout=10000)
        page.get_by_text(re.compile(r"Found\s+Count:", re.IGNORECASE)).first.wait_for(timeout=timeout_ms)
        page.wait_for_timeout(1000)
    except Exception:
        logger.exception("Unable to return to the document list")


def _page_text(page: Page) -> str:
    return page.locator("body").inner_text(timeout=30000)


def _extract_document_counts(text: str) -> dict[str, int]:
    counts = {document_type: 0 for document_type in DOCUMENT_TYPES}
    for document_type in DOCUMENT_TYPES:
        pattern = re.compile(rf"{re.escape(document_type)}\s*-\s*(\d+)", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            counts[document_type] = int(match.group(1))
    return counts


def _extract_found_count(text: str) -> int:
    match = re.search(r"Found\s+Count:\s*(\d+)", text, flags=re.IGNORECASE)
    if not match:
        return 0
    return int(match.group(1))


def _extract_metadata(text: str, matter_number: str) -> MatterMetadata:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = _line_after_label(lines, "Title - Description")
    matter_type = _line_after_label(lines, "Type")
    category = _line_after_label(lines, "Category")
    initial = _line_after_label(lines, "Date Received")
    final = _line_after_label(lines, "Date Final Submission")

    if title is None:
        title = _guess_title(lines, matter_number)
    if initial is not None and not _looks_like_date(initial):
        initial = None
    if final is not None and not _looks_like_date(final):
        final = None

    dates = _date_values(lines)
    if initial is None and dates:
        initial = dates[0]
    if final is None and len(dates) > 1:
        final = dates[1]

    inferred_type, inferred_category = _infer_type_and_category(lines, matter_number, title)
    if matter_type is None:
        matter_type = inferred_type
    if category is None:
        category = inferred_category

    raw_fields = {
        "text_sample": "\n".join(lines[:80]),
    }
    return MatterMetadata(
        matter_number=matter_number,
        title=title,
        matter_type=matter_type,
        category=category,
        initial_filing_date=initial,
        final_filing_date=final,
        raw_fields=raw_fields,
    )


def _line_after_label(lines: list[str], label: str) -> str | None:
    for index, line in enumerate(lines):
        if line.lower() == label.lower() and index + 1 < len(lines):
            value = lines[index + 1].strip()
            if value and not _looks_like_label(value):
                return value
    return None


def _guess_title(lines: list[str], matter_number: str) -> str | None:
    for line in lines:
        if matter_number in line:
            continue
        if " - " in line and len(line) > 20:
            return line
    return None


def _looks_like_label(value: str) -> bool:
    labels = {
        "matter no",
        "status",
        "title - description",
        "type",
        "category",
        "date received",
        "date final submission",
        "decision date",
        "outcome",
    }
    return value.strip().lower() in labels


def _date_values(lines: list[str]) -> list[str]:
    dates: list[str] = []
    for line in lines:
        if _looks_like_date(line):
            dates.append(line)
    return dates


def _looks_like_date(value: str) -> bool:
    return bool(re.fullmatch(r"\d{2}/\d{2}/\d{4}", value.strip()))


def _infer_type_and_category(
    lines: list[str],
    matter_number: str,
    title: str | None,
) -> tuple[str | None, str | None]:
    try:
        start = lines.index(matter_number)
    except ValueError:
        return None, None

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].lower() == "back to search results":
            end = index
            break

    values = lines[start + 1 : end]
    title_index = values.index(title) if title in values else None
    before_title = values[:title_index] if title_index is not None else values
    matter_type = next(
        (value for value in before_title if not _looks_like_status(value) and not _looks_like_date(value)),
        None,
    )

    category = None
    date_indexes = [index for index, value in enumerate(values) if _looks_like_date(value)]
    if date_indexes:
        for value in values[date_indexes[-1] + 1 :]:
            if not _looks_like_status(value) and not _looks_like_date(value):
                category = value
                break

    return matter_type, category


def _looks_like_status(value: str) -> bool:
    lowered = value.lower()
    status_words = (
        "awaiting",
        "closed",
        "compliance",
        "decided",
        "discontinued",
        "open",
        "pending",
        "withdrawn",
    )
    return any(word in lowered for word in status_words)


def _safe_filename(filename: str) -> str:
    cleaned = re.sub(r"[^\w.\- ]+", "_", filename).strip()
    return cleaned or "downloaded_file"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
