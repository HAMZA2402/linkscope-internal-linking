# LinkScope V1 - Internal Linking Automation

A separate, no-paid-API Streamlit application to identify evidence-based internal link suggestions. Not part of the SEO Intelligence Platform V4.

## What it does

- Crawl a **bounded number of publicly available pages** on one domain, checking `robots.txt` and discovering URLs from sitemap files.
- Or upload a CSV containing page copy, with no crawling required.
- Find source-to-target opportunities using **anchor text that already occurs verbatim in unlinked paragraph/list copy**.
- Skip self-links, known already-linked targets, noindex targets, most utility/archive targets, and duplicate source-target pairs.
- Review confidence scores, the exact source sentence, and the destination URL; approve/reject opportunities in a table.
- Download a formatted Excel workbook or CSV for editors to implement manually.

It does **not** rewrite articles, automatically place links, verify ranking changes, or promise traffic gains. It uses deterministic topic matching, not paid AI.

## Files

- `app.py`: Streamlit web interface.
- `internal_link_engine.py`: CSV parser, bounded site crawler, anchor matching, ranking rules.
- `reporting.py`: Excel export.
- `requirements.txt`: required Python libraries.
- `fictional_demo_pages.csv`: made-up example content; safe to upload publicly.
- `tests/test_core.py`: automated tests.
- `.gitignore`: protects local files from accidental commits.

## Deploy online (no terminal needed)

1. Open [GitHub](https://github.com/new) and create a **new, separate** repository, e.g. `linkscope-internal-linking`, visibility **Public** for a portfolio demo.
2. Download and extract the LinkScope ZIP on your computer.
3. In the empty repository, click **uploading an existing file**, and select the files **inside** the extracted folder. Include the `tests` folder if GitHub accepts it; the app itself requires `app.py`, `internal_link_engine.py`, `reporting.py`, `requirements.txt`, and `fictional_demo_pages.csv` in the repository root. **Do not upload the ZIP itself.**
4. Commit changes.
5. Open [Streamlit Community Cloud](https://share.streamlit.io/), sign in with GitHub, select **Create app > Deploy a public app from GitHub**.
6. Set repository `YOUR_USERNAME/linkscope-internal-linking`, branch `main`, and main file `app.py`. Choose a unique optional app URL, e.g. `linkscope-internal-linking-YOURNAME`.
7. Click **Deploy**. This creates a *different* app from your existing SEO Intelligence Platform V4.
8. Click **Fictional demo > Analyze fictional example** to verify the app before any other use.
9. To enable **live crawling for the owner**, in your deployed app click **Manage app > Settings > Secrets** and add the following, using a strong secret known only to you (do **not** add the key to GitHub):

   ```toml
   CRAWL_ACCESS_KEY = "put-a-long-unique-password-here"
   ```

   Save/reboot the app as prompted. Select **Crawl a website**, enter the private key and verify you are authorized to crawl the selected website.

   Without this secret, live crawling is intentionally locked on public hosting; fictional demo and CSV audit still work.

Documentation: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy

## CSV mode: exact field names

The uploaded CSV should have at least `URL` and `Content` (or `HTML`). Optional: `Title`, `H1`, `Existing Links` (pipe- or semicolon-separated URLs), and `Focus Keywords` (comma-separated phrases). The parser also accepts `Address`, `Body Text`, `Title 1` and `H1-1` aliases.

| URL | Title | H1 | Content | Existing Links |
|---|---|---|---|---|
| `https://example.com/blog/a/` | Example A | Example A | We discuss estate planning choices with clients. | `https://example.com/page-b/` |
| `https://example.com/estate-planning/` | Estate Planning | Estate Planning | Estate planning can involve wishes and documents. | |

For copy provided as CSV, the tool cannot know whether text was already hyperlinked unless you add `Existing Links` or `HTML`. It displays a warning to verify duplicate links manually. A standard Screaming Frog metadata-only CSV and a GSC queries export are **not enough** to prove exact anchor positions. A full rendered HTML export or a CSV with body copy is needed.

## Live crawl limits and security

- Default max 25 HTML pages, hard max 60 pages per run; request delays; response sizes and redirects are bounded.
- Only HTTP(S) public DNS hosts; disallows loopback/private/reserved destinations, credentials, and nonstandard ports. Redirects are checked again.
- Restricts crawl to selected domain and its www variant; checks robots.txt before fetching each page and honors crawl-delay when provided.
- Discovers sitemaps in robots.txt and common paths. If no usable sitemap exists, follows on-site links from visited pages.
- Some sites require JavaScript rendering or block bots; use CSV mode in that case. Results are not a complete technical SEO crawl.
- Live crawling requires the owner-configured `CRAWL_ACCESS_KEY` secret; it is intentionally disabled until configured. This is a simple access gate, **not** production-grade authentication or rate limiting.
- A public Streamlit Community Cloud deployment is **not an authenticated private service**. Do not upload restricted client data or crawl clients without authorization. The hosting provider can see/process operational data. The app does not deliberately write uploaded data to persistent disk.
- To add authentication, durable workspaces, scheduling, bulk crawler infrastructure, and automated CMS publication would be a *separate* future version.

## Review suggestions responsibly

1. Use the filters to locate candidates and inspect the verbatim anchor evidence.
2. Verify the source sentence still exists on the live page and is not already linked.
3. Open target URL and check user intent, topical relevance, and contextual naturalness.
4. Edit statuses / notes in **Review workflow**.
5. Download Excel report and manually implement approved links in the CMS.
6. Re-crawl to check the final internal link graph.

## Test locally (optional)

```sh
python -m pip install -r requirements.txt
python -m pytest -q tests
streamlit run app.py
```

`pytest` is a test-only optional package; it is not required to deploy the app.
