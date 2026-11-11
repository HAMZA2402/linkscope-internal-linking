"""LinkScope: standalone Streamlit internal-link opportunity auditor."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from internal_link_engine import AuditInputError, crawl_website, pages_table, read_csv_pages, suggest_links
from reporting import create_report, safe_cell

st.set_page_config(page_title='LinkScope | Internal Link Audit', page_icon=':link:', layout='wide', initial_sidebar_state='expanded')

BASE = Path(__file__).parent
DEMO_PATH = BASE / 'fictional_demo_pages.csv'

st.markdown('''
<style>
.stApp { background-color: #f5f8fb; }
.block-container { padding-top: 1.6rem; max-width: 1320px; }
.hero { padding: 2.0rem 2.2rem; border-radius: 17px; background: linear-gradient(115deg, #122642, #076f75); color: #ffffff; margin-bottom: 1.25rem; }
.eyebrow { text-transform: uppercase; font-weight: 760; font-size: .73rem; letter-spacing: .16em; color: #a7e9e2; }
.hero h1 { margin: .3rem 0 .4rem; font-size: 2.2rem; color: #fff; font-weight: 780; line-height: 1.16; }
.hero p { color: #cde2ea; margin: 0; font-size: .98rem; }
.note { border-left: 3px solid #0b9494; padding: .8rem 1rem; border-radius: 7px; background: #e8f5f3; }
.section-label { font-size: .76rem; text-transform: uppercase; color: #5a7887; letter-spacing: .12em; font-weight: 750; margin-bottom: 8px; }
[data-testid="stMetric"] { background: white; border: 1px solid #dae5ea; padding: .75rem 1rem; border-radius: 12px; }
[data-testid="stSidebar"] { background-color: #eef3f8; }
</style>
''', unsafe_allow_html=True)

st.markdown('''
<div class="hero">
<div class="eyebrow">LINKSCOPE / INTERNAL LINK INTELLIGENCE / V1</div>
<h1>Find natural links already hiding in your content.</h1>
<p>Crawl public pages or upload page copy. Find existing, unlinked anchor text, verify the target page, and export a review-ready linking plan.</p>
</div>
''', unsafe_allow_html=True)

st.sidebar.markdown('### Workspace')
st.sidebar.caption('Separate from your SEO Intelligence Platform. No paid API, n8n, or Colab needed.')
max_per = st.sidebar.slider('Suggestions per source page', 1, 5, 3, help='Limit repetitive link recommendations from one page.')
min_score = st.sidebar.slider('Minimum relevance score', 5.5, 13.0, 7.5, 0.5, help='Raise this to generate fewer, more conservative suggestions.')
st.sidebar.divider()
st.sidebar.markdown('**Important safeguards**')
st.sidebar.caption('Live crawling respects robots.txt, uses bounded requests, and refuses non-public IPs. Suggestions are not automatically published.')
st.sidebar.caption('Public cloud hosting is not appropriate for sensitive client data without permission.')

mode = st.segmented_control('Choose input', ['Fictional demo', 'Upload page CSV', 'Crawl a website'], default='Fictional demo', required=True)

if mode == 'Fictional demo':
    st.markdown('#### 1. Try an example site')
    st.write('Load a completely fictional legal services website to see how matching and deduplication work.')
    st.download_button('Download the example CSV template', data=DEMO_PATH.read_bytes(), file_name='fictional_demo_pages.csv', mime='text/csv', use_container_width=False)
    run_requested = st.button('Analyze fictional example', type='primary', use_container_width=False)
    if run_requested:
        try:
            pages, warnings = read_csv_pages(DEMO_PATH.read_bytes())
            st.session_state['audit_pages'] = pages
            st.session_state['audit_warnings'] = warnings
            st.session_state['audit_label'] = 'Fictional demo (not a client)'
            st.session_state.pop('review_editor', None)
            st.session_state.pop('review_key', None)
        except AuditInputError as err:
            st.error(str(err))

elif mode == 'Upload page CSV':
    st.markdown('#### 1. Upload a page-content CSV')
    st.caption('Columns: URL, Title, H1, Content, Existing Links (optional), Focus Keywords (optional). Content is required for exact-anchor proof.')
    st.download_button('Download CSV template', DEMO_PATH.read_bytes(), 'internal_links_csv_template.csv', 'text/csv')
    file = st.file_uploader('Choose a CSV containing page text or HTML', type=['csv'], help='The tool does not accept just GSC Queries.csv: that file does not contain page copy.')
    run_requested = st.button('Analyze uploaded pages', type='primary', disabled=file is None)
    if run_requested and file:
        try:
            pages, warnings = read_csv_pages(file.getvalue())
            st.session_state['audit_pages'] = pages
            st.session_state['audit_warnings'] = warnings
            st.session_state['audit_label'] = file.name
            st.session_state.pop('review_editor', None)
            st.session_state.pop('review_key', None)
        except AuditInputError as err:
            st.error(str(err))

else:
    st.markdown('#### 1. Crawl a public website')
    st.caption('Use only on websites where you are allowed to crawl. Some sites block automated requests, require JavaScript, or restrict bots.')
    try:
        owner_secret = str(st.secrets.get('CRAWL_ACCESS_KEY', ''))
    except Exception:
        owner_secret = ''
    owner_secret = owner_secret or os.environ.get('LINKSCOPE_CRAWL_ACCESS_KEY', '')
    if not owner_secret:
        st.warning('Public crawling is locked until the app owner sets CRAWL_ACCESS_KEY in Streamlit app Secrets. Demo and CSV modes still work.')
    with st.form('crawl_form'):
        website = st.text_input('Website homepage', value='https://example.com', placeholder='https://yourwebsite.com')
        sitemap = st.text_input('Sitemap URL (optional)', value='', placeholder='https://yourwebsite.com/sitemap.xml')
        a, b = st.columns(2)
        max_pages = a.slider('Maximum pages', 2, 60, 25, 1)
        delay = b.slider('Minimum delay between requests (seconds)', 0.3, 3.0, 0.8, 0.1)
        entered_secret = st.text_input('Private crawl access key', type='password', help='Configured by the app owner in Streamlit Secrets; never commit it to GitHub.')
        allowed = st.checkbox('I am authorized to audit this public website and understand robots.txt will be respected.')
        run_requested = st.form_submit_button('Start bounded crawl', type='primary', disabled=not allowed or not owner_secret)
    if run_requested and allowed:
        if not owner_secret or entered_secret != owner_secret:
            st.error('Incorrect crawl access key. Live crawling is available to authorized owners only.')
            st.stop()
        with st.spinner('Checking robots.txt, discovering sitemap pages and collecting on-page copy...'):
            try:
                pages, warnings = crawl_website(website, max_pages=max_pages, min_delay=delay, sitemap_url=sitemap)
                st.session_state['audit_pages'] = pages
                st.session_state['audit_warnings'] = warnings
                st.session_state['audit_label'] = website
                st.session_state.pop('review_editor', None)
                st.session_state.pop('review_key', None)
            except AuditInputError as err:
                st.error(str(err))
                st.info('Try uploading a CSV containing exported page copy instead. You can use the template above in CSV mode.')

pages = st.session_state.get('audit_pages', [])
warnings = st.session_state.get('audit_warnings', [])
if not pages:
    st.markdown('''<div class="note"><b>Start here:</b> Click <b>Analyze fictional example</b> above. Once you understand the results, use the CSV upload or crawl your own authorized public site.</div>''', unsafe_allow_html=True)
    with st.expander('What this first version does and does not do'):
        st.write('It identifies plausible source-to-target page pairs using literal, previously unlinked phrases in existing paragraph/list text. It checks self-links, existing targets, noindex and certain navigational pages. It does not create/edit links in a CMS and cannot prove SEO gains or understand every semantic nuance.')
    st.stop()

pages_df = pages_table(pages)
recommendations = suggest_links(pages, max_per_source=max_per, min_score=min_score)

st.divider()
st.markdown('### 2. Review your results')
st.caption('Dataset: ' + str(st.session_state.get('audit_label', 'Your input')))
metrics = st.columns(4)
metrics[0].metric('Pages analyzed', f'{len(pages):,}')
metrics[1].metric('Candidate links', f'{len(recommendations):,}')
metrics[2].metric('High-confidence candidates', f'{int((recommendations["Priority"] == "High").sum()):,}' if len(recommendations) else '0')
metrics[3].metric('Source pages with candidates', f'{recommendations["Source URL"].nunique():,}' if len(recommendations) else '0')

for warning in warnings[:8]:
    st.warning(warning, icon=':material/info:')

if recommendations.empty:
    st.info('No suggestions met this threshold. Lower the minimum relevance score in the sidebar, or upload more related pages with readable content. Exact-match evidence is required.')

tabs = st.tabs(['Overview', 'Link opportunities', 'Pages audited', 'Review workflow', 'Downloads & method'])
with tabs[0]:
    c1, c2 = st.columns([1.1, 1])
    with c1:
        st.markdown('**Most frequently suggested destinations**')
        if len(recommendations):
            grouped = recommendations.groupby('Target Title').size().sort_values(ascending=False).head(8)
            st.bar_chart(grouped, horizontal=True, height=310)
        else:
            st.caption('No matches to plot for the current threshold.')
    with c2:
        st.markdown('**Top source pages by proposed additions**')
        if len(recommendations):
            t = recommendations.groupby('Source URL').size().sort_values(ascending=False).head(8).rename('Suggestions').reset_index()
            st.dataframe(t, hide_index=True, use_container_width=True, height=310)
        else:
            st.caption('No sources to display.')
    st.info('Each recommendation requires human review. Scores are internal heuristics, not search engine ranking predictions.')

with tabs[1]:
    if len(recommendations):
        c1, c2 = st.columns([1, 2])
        priorities = c1.multiselect('Confidence', ['High', 'Medium'], default=['High', 'Medium'])
        search_text = c2.text_input('Search anchor, title or URL', placeholder='e.g. estate planning')
        filtered = recommendations[recommendations['Priority'].isin(priorities)]
        if search_text.strip():
            mask = filtered.astype(str).apply(lambda col: col.str.contains(search_text, case=False, regex=False)).any(axis=1)
            filtered = filtered[mask]
        st.dataframe(filtered[['Source URL','Existing anchor','Target URL','Priority','Relevance Score','Evidence / existing sentence']],
                     hide_index=True, use_container_width=True, height=430,
                     column_config={'Source URL': st.column_config.LinkColumn('Source URL'), 'Target URL': st.column_config.LinkColumn('Target URL'),
                                    'Relevance Score': st.column_config.NumberColumn('Score', format='%.2f')})
        st.caption(f'{len(filtered):,} of {len(recommendations):,} candidates displayed. No links are inserted automatically.')

with tabs[2]:
    st.dataframe(pages_df, hide_index=True, use_container_width=True, height=480,
                 column_config={'URL': st.column_config.LinkColumn('Page URL')})
    st.caption('The words and link counts describe the extracted DOM/copy, not a complete technical SEO crawl.')

with tabs[3]:
    st.markdown('**Mark candidates Approved, Rejected, or Implemented before exporting.**')
    if recommendations.empty:
        st.caption('No candidates to review.')
        edited = recommendations.copy()
    else:
        record_hash = hash((tuple(p.url for p in pages), max_per, min_score))
        if st.session_state.get('review_key') != record_hash:
            st.session_state['review_key'] = record_hash
            st.session_state.pop('review_editor', None)
        editable = ['Review Status', 'Owner', 'Notes']
        edited = st.data_editor(
            recommendations, key='review_editor', hide_index=True, use_container_width=True,
            height=430, num_rows='fixed', disabled=[x for x in recommendations.columns if x not in editable],
            column_config={
                'Review Status': st.column_config.SelectboxColumn('Review Status', options=['Needs review','Approved','Rejected','Implemented'], required=True),
                'Source URL': st.column_config.LinkColumn('Source URL'),
                'Target URL': st.column_config.LinkColumn('Target URL'),
                'Relevance Score': st.column_config.NumberColumn('Score', format='%.2f'),
            }
        )
        st.caption('Updates in this table are temporary to your current browser session; export to keep them.')

with tabs[4]:
    st.markdown('**Download your action-ready outputs**')
    safe_export = edited.copy()
    for column in safe_export.select_dtypes(include=['object']).columns:
        safe_export[column] = safe_export[column].map(safe_cell)
    csv_bytes = safe_export.to_csv(index=False).encode('utf-8-sig')
    workbook = create_report(pages_df, edited, warnings)
    left, right = st.columns(2)
    left.download_button('Download formatted Excel audit', workbook,
                         file_name='LinkScope_Internal_Linking_Audit.xlsx',
                         mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         use_container_width=True)
    right.download_button('Download suggestions CSV', csv_bytes,
                          file_name='LinkScope_Internal_Link_Opportunities.csv',
                          mime='text/csv', use_container_width=True)
    with st.expander('How recommendations are created'):
        st.markdown('''
1. Read accessible pages (or CSV-provided body copy) and extract H1, title, paragraphs, and current links.
2. Generate topic phrases from each destination's H1, title, URL slug, or provided focus keywords.
3. Find **verbatim matches in unlinked text nodes** on another page, not arbitrary rewritten anchor text.
4. Exclude self-links, already-linked targets, noindex targets, and some utility/archive pages.
5. Rank the remaining candidate links by phrase match and simple topical overlap.
6. **Human SEO review is required** before inserting any links. The app does not publish to a CMS.

**Limitations:** JavaScript-only pages, bot blocking, orphan pages, complex templates, and CSV exports missing existing-link data can reduce completeness. A score is a relevance heuristic, not a ranking forecast.
''')
    st.caption('Privacy: The app processes uploads in its running session. The hosting provider can still process data and may record operational logs. Do not use restricted client data without authorization.')
