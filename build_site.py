#!/usr/bin/env python3
"""
build_site.py — render the report to static HTML for GitHub Pages.

Writes site/index.html + one page per lake group, with static (.html) links, plus
.nojekyll so Pages serves the files as-is. Run after daily_run.py in the workflow;
the Pages deploy step then publishes ./site.
"""
import os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import render

SITE = os.path.join(ROOT, "site")
os.makedirs(SITE, exist_ok=True)


def main():
    open(os.path.join(SITE, ".nojekyll"), "w").close()
    with open(os.path.join(SITE, "index.html"), "w") as f:
        f.write(render.index_html(static=True))
    for group in render.GROUPS:
        with open(os.path.join(SITE, f"{group}.html"), "w") as f:
            f.write(render.report_html(group, static=True))
    print("built site/:", ["index.html"] + [f"{g}.html" for g in render.GROUPS])


if __name__ == "__main__":
    main()
