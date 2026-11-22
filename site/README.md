# The goat-cloud landing page

`index.html` is the landing page for goat-cloud: one static file with inline CSS, SVG and script, light and dark, with no request to another host. It shows four screenshots from `docs/img/screens/`; `.github/workflows/pages.yml` copies them into the site next to the page, so the repository keeps one copy of each. To preview it, copy those four files into `site/img/` and serve `site/` with any static file server.

Every number on the page has its command or source in the page's Try it section. The terminal blocks are real output of `python3 demo/generate.py`. The first screen replays the demo scan: the resources, their positions in the scan and their findings are taken from `demo/data/compliance-report.json`; with reduced motion it shows the finished replay. The tag drawing in the hero is a drawing of one resource from the synthetic demo estate, not a capture.

The workflow builds the site for every pull request and every push to `main` that touches `site/`, `docs/img/screens/` or the workflow. A push to `main` deploys it when GitHub Pages is enabled with Settings > Pages > Source set to "GitHub Actions"; while Pages is not enabled the build passes with a notice and nothing is published.

Every `<pre>` on the page is marked `data-box="command"` or `data-box="output"`. A command box shows the whole command at any width: it wraps at spaces, each line of it a `<span class="ln">` with a hanging indent, and Copy copies the exact text. An output box wraps on a wide screen and scrolls inside its box on a narrow one, and its caption says so. The workflow checks both at 1440 and 390 px wide with `site/fitcheck.py`, which needs Chrome (`python3 site/fitcheck.py site index.html`).
