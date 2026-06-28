# senpilot-regulatory-agent

A lean Python MVP for a regulatory document retrieval email agent.

The agent polls an inbox, reads requests like `Can you give me Other Documents from M12205?`, navigates the Nova Scotia UARB public documents site with Playwright, downloads up to 10 requested documents, ZIPs them, and replies by email with a deterministic matter summary.

## Workflow

The LangGraph workflow is:

1. `parse_request_deterministic`
2. optional `parse_request_llm_fallback`
3. `validate_request`
4. `send_clarification_email` or `scrape_matter`
5. `retry_or_fail_scrape` if scraping fails
6. `download_documents`
7. `create_zip`
8. `write_success_response`
9. `send_success_email`

LangGraph is used to make the workflow explicit, modular, traceable, and easy to extend. It also makes failure routing clear: if Playwright fails, the graph routes to a failure email instead of crashing.

## Design Choices

Playwright is used because the UARB site is interactive and uses buttons, tabs, and document preview actions. Browser automation is deterministic because regulatory document retrieval needs reliability and repeatability. The implementation does not let an LLM control the browser.

During live verification, the FileMaker `GO GET IT` buttons did not emit a browser download event in headless Chromium. The scraper therefore opens each row's Preview view and downloads the PDF from the same streaming URL exposed by the page, using the active Playwright browser context.

For document selection, the MVP preserves the UARB site's default list order. Before downloading, it records the first `MAX_DOWNLOADS` document numbers currently shown in the requested tab, then downloads those exact document numbers in that same order. It does not apply its own relevance ranking.

The parser is deterministic-first because the request space is constrained. It extracts matter numbers like `M12205`, `m12205`, or `12205`, then normalizes the document type to one of:

- Exhibits
- Key Documents
- Other Documents
- Transcripts
- Recordings

A lightweight Hugging Face fallback can be enabled for ambiguous natural language with `USE_LLM_FALLBACK=true`, but the happy path does not require an LLM. The fallback only extracts JSON. It does not browse, scrape, download, or make workflow decisions. Install `requests` separately if you enable it.

Pydantic validates the parsed request before the workflow touches the website. It also validates matter metadata and download results as they move through the graph.

LangSmith tracing can be enabled later through environment variables. This MVP keeps those variables in `.env.example` but does not require tracing.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

Edit `.env` with email credentials and mailbox hosts.

## Environment Variables

- `AGENT_EMAIL`
- `AGENT_EMAIL_PASSWORD`
- `IMAP_HOST`
- `IMAP_PORT`
- `SMTP_HOST`
- `SMTP_PORT`
- `HEADLESS=true`
- `MAX_DOWNLOADS=10`
- `USE_LLM_FALLBACK=false`
- `HF_TOKEN` optional
- `LANGCHAIN_TRACING_V2=false`
- `LANGCHAIN_API_KEY` optional
- `LANGCHAIN_PROJECT=senpilot-regulatory-agent`

Optional local tuning:

- `DOWNLOAD_ROOT=downloads`
- `UARB_URL=https://uarb.novascotia.ca/fmi/webd/UARB15`
- `SCRAPE_TIMEOUT_MS=60000`
- `MAX_SCRAPE_RETRIES=2`

## Run

```bash
python -m app.main
```

The script polls unread emails, invokes the graph once per email, sends a success, clarification, or failure response, then marks the email as read after the workflow completes.

## Example Input Email

```text
Hi Agent, Can you give me Other Documents files from M12205? Thanks!
```

## Example Output Email

```text
Hi,

M12205 is about Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,275,000. It relates to Capital Expenditure within the Water category. The matter had an initial filing on 04/07/2025 and a final filing on 10/23/2025. I found 13 Exhibits, 5 Key Documents, 21 Other Documents, no Transcripts, no Recordings. I downloaded 10 out of the 21 Other Documents. I am attaching them as a ZIP here.

Best,
Senpilot Agent
```

## Known Limitations

- The UARB site is FileMaker WebDirect, so DOM selectors can change. The scraper uses text, position, and preview-link fallbacks, but a real production system should monitor selector failures.
- The metadata extractor is intentionally heuristic and only uses visible page text.
- The inbox poller is single-process and local.
- Attachments are stored locally under `downloads/`.
- The optional Hugging Face fallback requires installing `requests` manually.

## Future Improvements

This local MVP could later be scaled by placing the LangGraph worker behind a queue, storing files in object storage, and saving metadata/job status in a database. Additional improvements would include stronger selector monitoring, provider-specific email adapters, persisted job records, and integration tests against a controlled browser fixture.
