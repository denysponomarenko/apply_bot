import json
import re
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


def normalize_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def profile_value_for_label(profile: dict, label_text: str):
    normalized = normalize_text(label_text)

    direct_map = {
        "name": "full_name",
        "full name": "full_name",
        "legal name": "full_name",
        "first name": "preferred_first_name",
        "last name": "preferred_last_name",
        "email": "email",
        "phone": "phone",
        "linkedin": "linkedin",
        "github": "github",
        "website": "website",
        "portfolio": "portfolio",
        "location": "location",
        "city": "city",
        "state": "state",
        "current company": "current_company",
        "current title": "current_title",
        "school": "school",
        "university": "school",
        "degree": "degree",
    }

    for key_text, profile_key in direct_map.items():
        if key_text in normalized and profile.get(profile_key):
            return profile.get(profile_key)

    if "authorized" in normalized and "work" in normalized:
        return profile.get("work_authorization")

    if "sponsor" in normalized or "sponsorship" in normalized:
        return profile.get("need_sponsorship")

    custom_fields = profile.get("custom_fields") or {}
    for key, value in custom_fields.items():
        if normalize_text(key) == normalized:
            return value

    return None


def input_label(locator) -> str:
    pieces = [
        locator.get_attribute("aria-label"),
        locator.get_attribute("placeholder"),
        locator.get_attribute("name"),
    ]

    input_id = locator.get_attribute("id")
    if input_id:
        page = locator.page
        label = page.locator(f'label[for="{input_id}"]').first
        if label.count() > 0:
            pieces.append(label.inner_text())

    return " ".join([p for p in pieces if p]).strip()


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


def check_option_for_question(page, question_text, option_text):
    """
    Finds a question block containing question_text, then selects the option_text.
    Useful for checkbox/radio groups with custom labels.
    """
    normalized_question = normalize_text(question_text)
    normalized_option = normalize_text(option_text)

    def click_from_container(container):
        option_candidates = container.locator(
            f'label:has-text("{option_text}"), span:has-text("{option_text}"), text={option_text}'
        )

        for i in range(option_candidates.count()):
            option = option_candidates.nth(i)
            try:
                if option.is_visible():
                    option.click()
                    print(f"Answered '{question_text}' with '{option_text}'")
                    return True
            except Exception:
                continue

        # Explicit input matching for controls where the text is associated via aria
        # attributes instead of a clickable label wrapper.
        controls = container.locator('input[type="checkbox"], input[type="radio"]')
        for i in range(controls.count()):
            control = controls.nth(i)

            try:
                descriptor_parts = [
                    control.get_attribute("aria-label") or "",
                    control.get_attribute("value") or "",
                ]

                control_id = control.get_attribute("id")
                if control_id:
                    label = container.locator(f'label[for="{control_id}"]').first
                    if label.count() > 0:
                        descriptor_parts.append(label.inner_text())

                normalized_descriptor = normalize_text(" ".join(descriptor_parts))
                if normalized_option and normalized_option in normalized_descriptor:
                    control.check()
                    print(f"Checked '{option_text}' for '{question_text}'")
                    return True
            except Exception:
                continue

        # Fallback for custom checkbox/radio wrappers where visible text is nested.
        controls_or_options = container.locator('input[type="checkbox"], input[type="radio"], option')
        for i in range(controls_or_options.count()):
            control = controls_or_options.nth(i)
            descriptor = " ".join(
                [
                    control.get_attribute("aria-label") or "",
                    control.get_attribute("value") or "",
                    control.inner_text() if control.evaluate("el => el.tagName.toLowerCase()") == "option" else "",
                ]
            )
            if normalized_option and normalized_option in normalize_text(descriptor):
                tag = (control.evaluate("el => el.tagName") or "").lower()
                try:
                    if tag == "option":
                        select_id = control.evaluate("el => el.parentElement && el.parentElement.id")
                        if select_id:
                            container.locator(f"select#{select_id}").select_option(value=control.get_attribute("value"))
                            print(f"Selected '{option_text}' for '{question_text}'")
                            return True
                    else:
                        control.check()
                        print(f"Checked '{option_text}' for '{question_text}'")
                        return True
                except Exception:
                    continue

        return False

    try:
        # Prefer an exact text hit when possible.
        block = page.locator(f"text={question_text}").first
        if block.count() > 0:
            # The nearest ancestor does not always include answers; walk up a few levels.
            for level in range(1, 5):
                container = block.locator(f"xpath=ancestor::*[self::div or self::fieldset][{level}]")
                if container.count() > 0 and click_from_container(container):
                    return True

        # Fallback to fuzzy matching for minor text differences like punctuation/casing.
        candidates = page.locator("fieldset, div")
        for i in range(candidates.count()):
            candidate = candidates.nth(i)
            text = normalize_text(candidate.inner_text())
            if not text or normalized_question not in text:
                continue
            if click_from_container(candidate):
                return True
    except Exception:
        pass

    return False


def fill_detected_fields(page, profile):
    filled = 0
    controls = page.locator("input, textarea, select")
    control_count = controls.count()

    for i in range(control_count):
        control = controls.nth(i)

        try:
            if not control.is_visible():
                continue
        except Exception:
            continue

        tag = (control.evaluate("el => el.tagName") or "").lower()
        input_type = (control.get_attribute("type") or "text").lower()

        if input_type in {"hidden", "submit", "button", "file"}:
            continue

        label_text = input_label(control)
        value = profile_value_for_label(profile, label_text)
        if value in [None, ""]:
            continue

        if tag in {"input", "textarea"} and input_type not in {"checkbox", "radio"}:
            control.fill(str(value))
            print(f"Filled '{label_text}' with '{value}'")
            filled += 1
            continue

        if tag == "select":
            selected = False
            try:
                control.select_option(label=str(value))
                selected = True
            except Exception:
                pass

            if not selected:
                try:
                    control.select_option(value=str(value))
                    selected = True
                except Exception:
                    pass

            if selected:
                print(f"Selected '{value}' for '{label_text}'")
                filled += 1
            continue

        if input_type == "checkbox":
            answer = str(value).strip().lower() in {"yes", "true", "1"}
            if answer:
                control.check()
            else:
                control.uncheck()
            print(f"Set checkbox '{label_text}' to {answer}")
            filled += 1

    radio_groups = page.locator('fieldset, div[role="radiogroup"]')
    for i in range(radio_groups.count()):
        group = radio_groups.nth(i)
        text = group.inner_text()
        value = profile_value_for_label(profile, text)
        if value in [None, ""]:
            continue

        choice = "Yes" if str(value).strip().lower() in {"yes", "true", "1"} else "No"
        option = group.locator(f'label:has-text("{choice}"), span:has-text("{choice}")').first
        if option.count() > 0:
            option.click()
            print(f"Answered radio '{text[:50]}...' with '{choice}'")
            filled += 1

    print(f"Auto-detected and filled {filled} field(s).")


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

        check_option_for_question(
            page,
            "How did you hear about this opportunity? (Select all that apply)",
            "LinkedIn"
        )

        fill_detected_fields(page, profile)

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
