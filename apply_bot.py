import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

JOB_URL = "https://jobs.ashbyhq.com/whatnot/22d4509c-42bd-4680-bb92-74f1a0cc9ba6/application?utm_source=LinkedInJobWrapping"
PROFILE_PATH = "profile.json"
AUTO_SUBMIT = False  # keep False until you trust the flow


def load_profile(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    required = ["full_name", "email", "phone", "resume_path"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"Missing required fields in profile.json: {missing}")

    resume = Path(data["resume_path"])
    if not resume.exists():
        raise FileNotFoundError(f"Resume file not found: {resume}")

    return data


def get_preferred_name_parts(profile: dict) -> tuple[str | None, str | None]:
    first_name = profile.get("preferred_first_name")
    last_name = profile.get("preferred_last_name")

    if first_name and last_name:
        return first_name, last_name

    full_name = (profile.get("full_name") or "").strip()
    if not full_name:
        return first_name, last_name

    parts = full_name.split()
    inferred_first = parts[0] if parts else None
    inferred_last = " ".join(parts[1:]) if len(parts) > 1 else None

    return first_name or inferred_first, last_name or inferred_last


def fill_first(page, selectors, value, label=None):
    if not value:
        return False

    for sel in selectors:
        try:
            locator = page.locator(sel).first
            if locator.count() > 0 and locator.is_visible():
                locator.fill(value)
                if label:
                    print(f"Filled {label} using selector: {sel}")
                return True
        except Exception:
            pass
    return False


def upload_first(page, selectors, filepath, label=None):
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            if locator.count() > 0:
                locator.set_input_files(filepath)
                if label:
                    print(f"Uploaded {label} using selector: {sel}")
                return True
        except Exception:
            pass
    return False


def check_yes_no_by_text(page, question_text, answer_yes=True):
    """
    Tries to find a question block containing question_text, then clicks Yes/No.
    This is heuristic and may need tuning per employer form.
    """
    try:
        block = page.locator(f"text={question_text}").first
        if block.count() == 0:
            return False

        container = block.locator("xpath=ancestor::*[self::div or self::fieldset][1]")
        answer_text = "Yes" if answer_yes else "No"

        candidate = container.locator(f"text={answer_text}").first
        if candidate.count() > 0 and candidate.is_visible():
            candidate.click()
            print(f"Answered '{question_text}' -> {answer_text}")
            return True
    except Exception:
        pass
    return False


def main():
    profile = load_profile(PROFILE_PATH)
    preferred_first_name, preferred_last_name = get_preferred_name_parts(profile)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=150)
        context = browser.new_context()
        page = context.new_page()

        print(f"Opening {JOB_URL}")
        page.goto(JOB_URL, wait_until="domcontentloaded", timeout=60000)

        # Give React time to hydrate
        page.wait_for_timeout(2500)

        # Common Ashby-ish fields
        fill_first(
            page,
            [
                'input[name*="first"]',
                'input[aria-label*="First Name"]',
                'input[placeholder*="First Name"]',
                'input[name="name"]',
                'input[aria-label*="Full Legal Name"]',
                'input[placeholder*="Full Legal Name"]',
                'input[type="text"]'
            ],
            preferred_first_name,
            "preferred_first_name"
        )

        fill_first(
            page,
            [
                'input[name*="last"]',
                'input[aria-label*="Last Name"]',
                'input[placeholder*="Last Name"]'
            ],
            preferred_last_name,
            "preferred_last_name"
        )

        fill_first(
            page,
            [
                'input[name="name"]',
                'input[aria-label*="Full Legal Name"]',
                'input[placeholder*="Full Legal Name"]'
            ],
            profile.get("full_name"),
            "full_name"
        )

        fill_first(
            page,
            [
                'input[name="email"]',
                'input[type="email"]',
                'input[aria-label*="Email"]',
                'input[placeholder*="Email"]'
            ],
            profile.get("email"),
            "email"
        )

        fill_first(
            page,
            [
                'input[name="phone"]',
                'input[type="tel"]',
                'input[aria-label*="Phone"]',
                'input[placeholder*="Phone"]'
            ],
            profile.get("phone"),
            "phone"
        )

        fill_first(
            page,
            [
                'input[name*="linkedin"]',
                'input[aria-label*="LinkedIn"]',
                'input[placeholder*="LinkedIn"]'
            ],
            profile.get("linkedin"),
            "linkedin"
        )

        fill_first(
            page,
            [
                'input[name*="location"]',
                'input[aria-label*="Location"]',
                'input[placeholder*="Location"]'
            ],
            profile.get("location"),
            "location"
        )

        # Resume upload
        uploaded = upload_first(
            page,
            [
                'input[type="file"]',
                'input[name*="resume"]'
            ],
            profile["resume_path"],
            "resume"
        )

        if not uploaded:
            print("Resume upload input not found automatically.")

        # Example heuristics for common screening questions
        check_yes_no_by_text(
            page,
            "Are you legally authorized to work",
            answer_yes=(profile.get("work_authorization", "").lower() == "yes")
        )

        check_yes_no_by_text(
            page,
            "Will you now or in the future require sponsorship",
            answer_yes=(profile.get("need_sponsorship", "").lower() == "yes")
        )

        # Let autocomplete/render finish
        page.wait_for_timeout(1500)

        # Save screenshot for review
        screenshot_path = "application_filled_preview.png"
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"Saved preview screenshot to {screenshot_path}")

        if AUTO_SUBMIT:
            try:
                submit_btn = page.locator(
                    'button:has-text("Submit"), button:has-text("Apply"), input[type="submit"]'
                ).first
                if submit_btn.count() > 0 and submit_btn.is_enabled():
                    submit_btn.click()
                    print("Submitted application.")
                else:
                    print("Submit button not found or not enabled.")
            except PlaywrightTimeoutError:
                print("Timed out trying to submit.")
        else:
            print("AUTO_SUBMIT is False. Review the browser and submit manually if everything looks correct.")
            time.sleep(60)

        browser.close()


if __name__ == "__main__":
    main()
