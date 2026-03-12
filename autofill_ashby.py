#!/usr/bin/env python3
"""Autofill the Whatnot Ashby application form from profile.json."""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://jobs.ashbyhq.com/whatnot/22d4509c-42bd-4680-bb92-74f1a0cc9ba6/application?utm_source=LinkedInJobWrapping"


def load_profile(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    profile = load_profile(repo_root / "profile.json")

    full_name = profile.get("full_name", "")
    first_name, last_name = split_name(full_name)
    resume_path = profile.get("resume_path", "")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded")

        page.get_by_label("Full Legal Name").fill(full_name)
        page.get_by_label("Email").fill(profile.get("email", ""))
        page.get_by_label("Phone Number").fill(profile.get("phone", ""))
        page.get_by_label("Preferred First Name").fill(first_name)
        page.get_by_label("Preferred Last Name").fill(last_name)
        page.get_by_label("Linkedin Profile or Website").fill(profile.get("linkedin", ""))

        if resume_path:
            resume_file = Path(resume_path).expanduser()
            if resume_file.exists():
                page.locator("#_systemfield_resume").set_input_files(str(resume_file))
            else:
                print(f"Resume file not found at {resume_file}. Upload manually.")

        page.get_by_label("LinkedIn").check()

        location_text = "N/A - I am not in one of the hub locations but I AM able to relocate"
        page.get_by_label(location_text).check()

        page.get_by_label("Please list the city and state/province that you are located in today.").fill(
            profile.get("location", "")
        )

        if profile.get("work_authorization", "").strip().lower() == "yes":
            page.get_by_label("Are you legally authorized to work in the United States?").check()

        if profile.get("need_sponsorship", "").strip().lower() == "no":
            page.get_by_label("Will you now or in the future require visa sponsorship to work in the United States?").check()

        print("Form autofill complete. Review fields, complete captcha, then submit manually.")
        page.wait_for_timeout(180000)
        browser.close()


if __name__ == "__main__":
    main()
