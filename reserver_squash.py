#!/usr/bin/env python3
"""
Réservation automatique Squash - Resamania / La Ruche aux Sports.

Objectif : réserver 1 place sur 2 créneaux le lundi dans 8 jours :
- 18h10
- 17h30

Priorité des courts : 3, 4, 5, 2, 1.
Un court libre est identifié par le libellé "2 places restantes".

Le site étant une SPA JavaScript, le script utilise Playwright.
Lance d'abord :
    python reserver_squash.py --dry-run --headed --debug
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

from dotenv import load_dotenv
from playwright.sync_api import Browser, BrowserContext, Locator, Page, TimeoutError, sync_playwright


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
SCREENSHOT_DIR = BASE_DIR / "screenshots"
LOG_DIR.mkdir(exist_ok=True)
SCREENSHOT_DIR.mkdir(exist_ok=True)

DEFAULT_URL = "https://member.resamania.com/larucheauxsports/"
PLANNING_BASE_URL = "https://member.resamania.com/larucheauxsports/planning"
DEFAULT_CLUB_PATH = "/larucheauxsports/clubs/2508"
DEFAULT_ACTIVITY_GROUP_PATH = "/larucheauxsports/activity_groups/392"
DEFAULT_MOMENT = "17:00-19:00"
ACTIVITY = "Squash"
SLOTS = ["18:10", "17:30"]
COURT_PRIORITY = ["3", "4", "5", "2", "1"]
REQUIRED_REMAINING_PLACES = 2
TIME_FILTER_BY_SLOT = {
    "18:10": "Soir",
    "17:30": "L'après-midi",
}

# Libellés probables observables dans des interfaces FR.
LOGIN_BUTTON_TEXTS = [
    "Connexion",
    "Se connecter",
    "Connectez-vous",
    "Renseigner mon mot de passe",
    "Mot de passe",
    "Valider",
    "Login",
]
BOOKING_BUTTON_TEXTS = ["Réserver", "Reservation", "Réservation", "Planning", "Agenda"]
CONFIRM_BUTTON_TEXTS = ["Confirmer", "Valider", "Réserver", "Payer", "Terminer"]


@dataclass(frozen=True)
class Settings:
    url: str
    email: str
    password: str
    headless: bool
    dry_run: bool
    debug: bool
    target_date: date
    page_wait_ms: int = 3000
    expected_cards_per_slot: int = 5
    card_load_max_scrolls: int = 35
    card_load_scroll_px: int = 700
    card_scroll_wait_ms: int = 1200
    planning_base_url: str = PLANNING_BASE_URL
    club_path: str = DEFAULT_CLUB_PATH
    activity_group_path: str = DEFAULT_ACTIVITY_GROUP_PATH
    moment: str = DEFAULT_MOMENT
    use_direct_planning_url: bool = True


def setup_logger(debug: bool) -> logging.Logger:
    logger = logging.getLogger("squash_booking")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG if debug else logging.INFO)

    file_handler = logging.FileHandler(LOG_DIR / "booking.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on", "oui"}


def monday_in_8_days_from_sunday(today: date | None = None) -> date:
    """
    Date cible par défaut.

    - Si le script tourne dimanche : lundi 8 jours après, donc J+8.
    - Les autres jours, pour les tests manuels : prochain lundi visible après navigation
      vers la semaine suivante.

    Pour forcer une date précise : python reserver_squash.py --date YYYY-MM-DD
    """
    today = today or date.today()
    if today.weekday() == 6:  # dimanche
        return today + timedelta(days=8)
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    return today + timedelta(days=days_until_monday)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Réservation automatique Squash Resamania")
    parser.add_argument("--headed", action="store_true", help="Lance Chromium en mode visible")
    parser.add_argument("--headless", action="store_true", help="Force le mode headless")
    parser.add_argument("--dry-run", action="store_true", help="Teste sans confirmer la réservation")
    parser.add_argument("--debug", action="store_true", help="Logs détaillés + pauses courtes")
    parser.add_argument("--date", help="Date cible ISO YYYY-MM-DD, pour test manuel")
    return parser.parse_args()


def load_settings(args: argparse.Namespace) -> Settings:
    load_dotenv(BASE_DIR / ".env")
    email = os.getenv("RESAMANIA_EMAIL", "").strip()
    password = os.getenv("RESAMANIA_PASSWORD", "").strip()
    if not email or not password:
        raise RuntimeError("RESAMANIA_EMAIL et RESAMANIA_PASSWORD doivent être définis dans .env")

    env_headless = parse_bool(os.getenv("HEADLESS"), default=True)
    headless = env_headless
    if args.headed:
        headless = False
    if args.headless:
        headless = True

    dry_run = args.dry_run or parse_bool(os.getenv("DRY_RUN"), default=False)
    target = date.fromisoformat(args.date) if args.date else monday_in_8_days_from_sunday()

    return Settings(
        url=os.getenv("RESAMANIA_URL", DEFAULT_URL).strip() or DEFAULT_URL,
        email=email,
        password=password,
        headless=headless,
        dry_run=dry_run,
        debug=args.debug,
        target_date=target,
        page_wait_ms=int(os.getenv("PAGE_WAIT_MS", "3000")),
        expected_cards_per_slot=int(os.getenv("EXPECTED_CARDS_PER_SLOT", "5")),
        card_load_max_scrolls=int(os.getenv("CARD_LOAD_MAX_SCROLLS", "35")),
        card_load_scroll_px=int(os.getenv("CARD_LOAD_SCROLL_PX", "700")),
        card_scroll_wait_ms=int(os.getenv("CARD_SCROLL_WAIT_MS", "1200")),
        planning_base_url=os.getenv("PLANNING_BASE_URL", PLANNING_BASE_URL).strip() or PLANNING_BASE_URL,
        club_path=os.getenv("RESAMANIA_CLUB_PATH", DEFAULT_CLUB_PATH).strip() or DEFAULT_CLUB_PATH,
        activity_group_path=os.getenv("RESAMANIA_ACTIVITY_GROUP_PATH", DEFAULT_ACTIVITY_GROUP_PATH).strip() or DEFAULT_ACTIVITY_GROUP_PATH,
        moment=os.getenv("RESAMANIA_MOMENT", DEFAULT_MOMENT).strip() or DEFAULT_MOMENT,
        use_direct_planning_url=parse_bool(os.getenv("USE_DIRECT_PLANNING_URL"), default=True),
    )




def configurable_wait(page: Page, settings: Settings, logger: logging.Logger, reason: str = "") -> None:
    if settings.page_wait_ms > 0:
        logger.info(
            f"Attente de {settings.page_wait_ms} ms"
            + (f" avant {reason}" if reason else "")
        )
        page.wait_for_timeout(settings.page_wait_ms)

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip().lower()


def screenshot(page: Page, name: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOT_DIR / f"{stamp}_{name}.png"
    page.screenshot(path=str(path), full_page=True)
    return path


def click_first_text(page: Page, texts: Iterable[str], timeout_ms: int = 2500) -> bool:
    for text in texts:
        candidates = [
            page.get_by_role("button", name=re.compile(re.escape(text), re.I)),
            page.get_by_text(re.compile(re.escape(text), re.I)),
        ]
        for candidate in candidates:
            try:
                if candidate.count() > 0:
                    candidate.first.click(timeout=timeout_ms)
                    return True
            except Exception:
                continue
    return False


def wait_dom(page: Page) -> None:
    page.wait_for_load_state("domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except TimeoutError:
        pass



def accept_cookies_if_present(page: Page) -> None:
    for text in ["Tout accepter", "Accepter", "J'accepte", "OK", "Autoriser"]:
        try:
            btn = page.get_by_role("button", name=re.compile(re.escape(text), re.I))
            if btn.count() > 0 and btn.first.is_visible(timeout=800):
                btn.first.click(timeout=1500)
                return
        except Exception:
            continue


def find_visible_input_in_frames(page: Page, selectors: list[str], timeout_ms: int = 30_000) -> Locator | None:
    """Cherche un input visible dans la page ou dans les iframes."""
    deadline = datetime.now().timestamp() + timeout_ms / 1000
    while datetime.now().timestamp() < deadline:
        for frame in page.frames:
            for selector in selectors:
                try:
                    loc = frame.locator(selector)
                    if loc.count() > 0 and loc.first.is_visible(timeout=500):
                        return loc.first
                except Exception:
                    continue
        page.wait_for_timeout(500)
    return None

def login(page: Page, settings: Settings, logger: logging.Logger) -> None:
    logger.info("Ouverture de Resamania")
    page.goto(settings.url, wait_until="commit", timeout=60_000)
    wait_dom(page)
    configurable_wait(page, settings, logger, "le chargement initial")
    accept_cookies_if_present(page)

    # Certains sites affichent directement le formulaire, d'autres nécessitent un clic.
    click_first_text(page, LOGIN_BUTTON_TEXTS, timeout_ms=3000)
    configurable_wait(page, settings, logger, "la recherche des champs de connexion")

    configurable_wait(page, settings, logger, "la stabilisation de la page")

    logger.info("Connexion")
    email_inputs = [
        "input[type='email']",
        "input[name*='email' i]",
        "input[name*='login' i]",
        "input[autocomplete='username']",
        "input[type='text']",
    ]
    password_inputs = [
        "input[type='password']",
        "input[name*='password' i]",
        "input[autocomplete='current-password']",
    ]

    # Resamania utilise souvent une connexion en 2 étapes :
    # 1) saisir l'email ; 2) cliquer sur "Renseigner mon mot de passe" ; 3) saisir le mot de passe.
    email_input = find_visible_input_in_frames(page, email_inputs, timeout_ms=45_000)
    if not email_input:
        path = screenshot(page, "email_field_not_found")
        raise RuntimeError(
            f"Champ email introuvable après attente. Page actuelle={page.url}. Screenshot : {path}"
        )

    email_input.fill(settings.email)
    page.wait_for_timeout(500)

    password_input = find_visible_input_in_frames(page, password_inputs, timeout_ms=2500)
    if not password_input:
        logger.debug("Champ mot de passe absent : clic sur l'étape 'Renseigner mon mot de passe'")
        clicked_next = click_first_text(
            page,
            ["Renseigner mon mot de passe", "Continuer", "Suivant", "Valider", "Connexion"],
            timeout_ms=5000,
        )
        if not clicked_next:
            # Fallback : appuyer sur Entrée depuis le champ email.
            try:
                email_input.press("Enter")
            except Exception:
                page.keyboard.press("Enter")
        wait_dom(page)
        page.wait_for_timeout(2500)
        password_input = find_visible_input_in_frames(page, password_inputs, timeout_ms=45_000)

    if not password_input:
        path = screenshot(page, "password_field_not_found")
        raise RuntimeError(
            f"Champ mot de passe introuvable après saisie email. "
            f"Page actuelle={page.url}. Screenshot : {path}"
        )

    password_input.fill(settings.password)
    page.wait_for_timeout(500)

    if not click_first_text(page, ["Connexion", "Se connecter", "Valider", "Login"], timeout_ms=5000):
        # fallback : Entrée dans le champ mot de passe
        try:
            password_input.press("Enter")
        except Exception:
            page.keyboard.press("Enter")
    wait_dom(page)
    page.wait_for_timeout(3000)

    body = normalize(page.locator("body").inner_text(timeout=10_000))
    if any(term in body for term in ["mot de passe incorrect", "identifiant incorrect", "invalid"]):
        path = screenshot(page, "login_failed")
        raise RuntimeError(f"Connexion probablement échouée. Screenshot : {path}")
    logger.info("Connexion effectuée ou session déjà active")

def open_booking_area(page: Page, logger: logging.Logger) -> None:
    logger.info("Accès à l'espace réservation")
    # Heuristique : cliquer sur Réservation / Planning / Agenda si présent.
    click_first_text(page, BOOKING_BUTTON_TEXTS, timeout_ms=2500)
    wait_dom(page)


def close_modal_if_present(page: Page, logger: logging.Logger) -> None:
    """Ferme une modale éventuelle ouverte par erreur."""
    for selector in [
        "button[aria-label*='close' i]",
        "button[aria-label*='fermer' i]",
        ".modal button:has-text('×')",
        "text=×",
    ]:
        try:
            loc = page.locator(selector)
            if loc.count() > 0 and loc.first.is_visible(timeout=500):
                loc.first.click(timeout=1500)
                page.wait_for_timeout(500)
                logger.debug("Modale fermée avec le sélecteur %s", selector)
                return
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass


def click_activity_combobox(page: Page, logger: logging.Logger) -> bool:
    """Clique explicitement la combobox Activités, sans cliquer une carte Squash."""
    selectors = [
        "mat-select:near(:text('Activités'))",
        "[role='combobox']:near(:text('Activités'))",
        "mat-select",
        "[role='combobox']",
    ]
    for selector in selectors:
        try:
            loc = page.locator(selector)
            count = min(loc.count(), 5)
            for i in range(count):
                candidate = loc.nth(i)
                if not candidate.is_visible(timeout=500):
                    continue
                txt = normalize(candidate.inner_text(timeout=1000))
                # La première combobox visible est normalement "Activités".
                if selector in {"mat-select", "[role='combobox']"} and i > 0 and "activité" not in txt and "toutes les activités" not in txt:
                    continue
                candidate.click(timeout=3000)
                logger.debug("Combobox activité ouverte via %s", selector)
                return True
        except Exception:
            continue
    return False


def select_activity(page: Page, activity: str, settings: Settings, logger: logging.Logger) -> None:
    logger.info("Sélection de l'activité via la combobox : %s", activity)
    close_modal_if_present(page, logger)
    configurable_wait(page, settings, logger, "la sélection de l'activité")

    opened = click_activity_combobox(page, logger)
    if not opened:
        path = screenshot(page, "activity_combobox_not_found")
        raise RuntimeError(f"Combobox Activités introuvable. Screenshot : {path}")

    page.wait_for_timeout(700)
    option_selectors = [
        f"mat-option:has-text('{activity}')",
        f"[role='option']:has-text('{activity}')",
        f"text=/^{activity}$/i",
        f"text={activity}",
    ]
    for selector in option_selectors:
        try:
            option = page.locator(selector)
            if option.count() > 0 and option.first.is_visible(timeout=2000):
                option.first.click(timeout=3000)
                wait_dom(page)
                configurable_wait(page, settings, logger, "le rechargement après filtre activité")
                close_modal_if_present(page, logger)
                return
        except Exception:
            continue

    path = screenshot(page, "activity_option_not_found")
    raise RuntimeError(f"Option d'activité '{activity}' introuvable après ouverture de la combobox. Screenshot : {path}")

def french_date_label(target: date) -> str:
    days = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
    return f"{days[target.weekday()]}. {target:%d/%m}"


def click_next_week(page: Page, logger: logging.Logger) -> bool:
    """Clique sur le bouton Resamania de semaine suivante.

    Sur cette page, le bouton `>` est rendu comme :
        <button value="1"> → </button>
    On cible donc en priorité `button[value="1"]`, puis on garde les anciens
    fallbacks pour rester compatible si le DOM change.
    """
    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    except Exception:
        pass

    selectors = [
        "button[value='1']",
        "button:has-text('→')",
        "button:has-text('>')",
        "button:has-text('›')",
        "button[aria-label*='next' i]",
        "button[aria-label*='suivant' i]",
        "button[aria-label*='droite' i]",
        "button:has(mat-icon:has-text('keyboard_arrow_right'))",
        "button:has(mat-icon:has-text('chevron_right'))",
        "xpath=//button[@value='1']",
        "xpath=//button[contains(normalize-space(.), '→') or contains(normalize-space(.), '>') or contains(normalize-space(.), '›')]",
    ]

    for selector in selectors:
        try:
            loc = page.locator(selector)
            count = min(loc.count(), 20)
            for i in reversed(range(count)):
                candidate = loc.nth(i)
                if not candidate.is_visible(timeout=500):
                    continue
                box = candidate.bounding_box()
                if not box:
                    continue
                # Évite les boutons de la barre haute, ex. Se déconnecter.
                if box["y"] < 180:
                    continue
                candidate.scroll_into_view_if_needed(timeout=2000)
                candidate.click(timeout=4000, force=True)
                wait_dom(page)
                logger.debug("Semaine suivante cliquée via %s, bbox=%s", selector, box)
                return True
        except Exception as exc:
            logger.debug("Méthode bouton semaine suivante ignorée (%s) : %s", selector, exc)
            continue

    path = screenshot(page, "next_week_button_not_found")
    logger.error("Bouton semaine suivante introuvable. Screenshot : %s", path)
    return False


def click_target_day_button(page: Page, target: date, logger: logging.Logger) -> bool:
    """Clique le bouton de date cible.

    Sur Resamania, le bouton du jour a la forme :
        <button value="YYYY-MM-DD" aria-pressed="true|false">Lun. DD/MM</button>
    On cible donc en priorité `button[value='<date iso>']`.
    """
    iso = target.isoformat()
    ddmm = target.strftime("%d/%m")
    label = french_date_label(target)

    selectors = [
        f"button[value='{iso}']",
        f"button[value='{iso}'][aria-pressed]",
        f"xpath=//button[@value='{iso}']",
        f"xpath=//button[contains(normalize-space(.), '{ddmm}') and contains(normalize-space(.), 'Lun')]" if target.weekday() == 0 else f"xpath=//button[contains(normalize-space(.), '{ddmm}')]",
    ]

    for selector in selectors:
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                candidate = loc.first
                if candidate.is_visible(timeout=1000):
                    candidate.scroll_into_view_if_needed(timeout=2000)
                    candidate.click(timeout=4000, force=True)
                    wait_dom(page)
                    logger.debug("Date cible cliquée via %s", selector)
                    return True
        except Exception as exc:
            logger.debug("Méthode date cible ignorée (%s) : %s", selector, exc)
            continue

    patterns = [
        re.compile(rf"{re.escape(label)}", re.I),
        re.compile(rf"\b{re.escape(ddmm)}\b", re.I),
        re.compile(rf"\bLun\.?\s*{re.escape(ddmm)}\b", re.I) if target.weekday() == 0 else re.compile(rf"\b{re.escape(ddmm)}\b", re.I),
    ]
    for pattern in patterns:
        for getter in [page.get_by_role("button", name=pattern), page.get_by_text(pattern)]:
            try:
                if getter.count() > 0 and getter.first.is_visible(timeout=1000):
                    getter.first.click(timeout=3000, force=True)
                    wait_dom(page)
                    logger.debug("Date cible cliquée : %s", pattern.pattern)
                    return True
            except Exception:
                continue
    return False


def select_date(page: Page, target: date, settings: Settings, logger: logging.Logger) -> None:
    logger.info("Sélection de la date cible : %s (%s)", target.isoformat(), french_date_label(target))
    close_modal_if_present(page, logger)
    configurable_wait(page, settings, logger, "la sélection de la date")

    # Parcours Resamania observé :
    # 1. cliquer le bouton semaine suivante <button value="1"> → </button> ;
    # 2. cliquer le bouton date <button value="YYYY-MM-DD" ...>.
    for attempt in range(7):
        logger.debug("Tentative sélection date %s/%s", attempt + 1, 7)
        if click_target_day_button(page, target, logger):
            configurable_wait(page, settings, logger, "le chargement du planning de la date")
            return
        if attempt < 6:
            if not click_next_week(page, logger):
                break
            configurable_wait(page, settings, logger, "le chargement de la semaine suivante")

    # Fallback : input date HTML éventuel.
    date_inputs = page.locator("input[type='date']")
    try:
        if date_inputs.count() > 0:
            date_inputs.first.fill(target.isoformat())
            page.keyboard.press("Enter")
            wait_dom(page)
            configurable_wait(page, settings, logger, "le chargement après saisie date")
            return
    except Exception:
        pass

    path = screenshot(page, "target_date_not_found")
    raise RuntimeError(f"Date cible non sélectionnée : {french_date_label(target)} / value={target.isoformat()}. Screenshot : {path}")

def slot_regex(slot: str) -> re.Pattern[str]:
    h, m = slot.split(":")
    return re.compile(rf"\b0?{int(h)}\s*[h:]\s*{m}\b", re.I)


def remaining_places_regex() -> re.Pattern[str]:
    return re.compile(rf"\b{REQUIRED_REMAINING_PLACES}\s+places?\s+restantes?\b", re.I)


def court_regex(court: str) -> re.Pattern[str]:
    return re.compile(rf"\b(court|terrain)\s*{court}\b|\b{court}\b", re.I)


def visible_body_text(page: Page) -> str:
    try:
        return normalize(page.locator("body").inner_text(timeout=5000))
    except Exception:
        return ""



def loaded_slot_cards(page: Page, slot: str, logger: logging.Logger) -> tuple[int, set[str]]:
    """Retourne le nombre de cartes Squash chargées pour un créneau et les courts détectés."""
    courts: set[str] = set()
    count = 0
    try:
        cards = page.locator(f'div.MuiPaper-root:has-text("{ACTIVITY}"):has-text("{slot}")')
        total = min(cards.count(), 200)
        for i in range(total):
            try:
                card = cards.nth(i)
                text = normalize(card.inner_text(timeout=500))
                if not text or "squash" not in text or not slot_regex(slot).search(text):
                    continue
                m = re.search(r"\b([1-5])\s+court\s+squash\b", text, re.I)
                if m:
                    courts.add(m.group(1))
                    count += 1
            except Exception as exc:
                logger.debug("Impossible de lire une carte %s index %s : %s", slot, i, exc)
    except Exception as exc:
        logger.debug("Comptage des cartes impossible pour %s : %s", slot, exc)
    return count, courts


def ensure_slot_cards_loaded(page: Page, slot: str, settings: Settings, logger: logging.Logger) -> None:
    """
    Resamania charge la grille progressivement au scroll.
    Pour un créneau donné, on attend normalement 5 cartes : courts 1 à 5.
    Si moins de 5 cartes sont dans le DOM, on scrolle vers le bas jusqu'à chargement.
    """
    expected = settings.expected_cards_per_slot
    logger.debug(
        "Préchargement cartes slot=%s : expected=%s, max_scrolls=%s, scroll_px=%s, scroll_wait_ms=%s",
        slot, expected, settings.card_load_max_scrolls, settings.card_load_scroll_px, settings.card_scroll_wait_ms,
    )

    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    except Exception as exc:
        logger.debug("Scroll top impossible avant préchargement %s : %s", slot, exc)

    best_count = 0
    best_courts: set[str] = set()
    stagnant_steps = 0

    for step in range(settings.card_load_max_scrolls + 1):
        count, courts = loaded_slot_cards(page, slot, logger)
        if count > best_count or courts != best_courts:
            stagnant_steps = 0
            best_count, best_courts = count, set(courts)
        else:
            stagnant_steps += 1

        logger.debug(
            "Chargement cartes %s étape %02d : count=%s/%s courts=%s scrollY=%s",
            slot, step, count, expected, sorted(courts),
            page.evaluate("Math.round(window.scrollY)") if step % 1 == 0 else "?",
        )

        if count >= expected or len(courts) >= expected:
            logger.debug("Cartes suffisantes chargées pour %s : count=%s, courts=%s", slot, count, sorted(courts))
            return

        try:
            at_bottom = page.evaluate("window.innerHeight + window.scrollY >= document.body.scrollHeight - 5")
            if at_bottom and stagnant_steps >= 2:
                logger.debug(
                    "Bas de page atteint avant %s cartes pour %s : meilleur count=%s, courts=%s",
                    expected, slot, best_count, sorted(best_courts),
                )
                break
        except Exception as exc:
            logger.debug("Détection bas de page impossible pour %s : %s", slot, exc)

        logger.debug("Scroll chargement cartes %s : deltaY=%s puis attente %sms", slot, settings.card_load_scroll_px, settings.card_scroll_wait_ms)
        page.mouse.wheel(0, settings.card_load_scroll_px)
        page.wait_for_timeout(settings.card_scroll_wait_ms)

    logger.warning(
        "Moins de %s cartes chargées pour %s après scroll : meilleur count=%s, courts=%s. Recherche ciblée maintenue.",
        expected, slot, best_count, sorted(best_courts),
    )

def has_required_places(text: str) -> bool:
    """Vrai uniquement si la carte indique explicitement 2 places restantes."""
    normalized = normalize(text).lower()
    return bool(remaining_places_regex().search(normalized))


def card_has_target_day(text: str, target: date) -> bool:
    """Vrai si le texte contient le jour cible, ex. 'mercredi 20 mai'."""
    normalized = normalize(text).lower()
    weekdays = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    months = [
        "janvier",
        "fevrier",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "aout",
        "septembre",
        "octobre",
        "novembre",
        "decembre",
    ]
    day_name = weekdays[target.weekday()]
    month_name = months[target.month - 1]

    # Supporte les variantes fréquentes : 'mercredi 20 mai' ou 'mer. 20/05'.
    full_label = f"{day_name} {target.day} {month_name}"
    short_slash = target.strftime("%d/%m")
    short_dot = f"{day_name[:3]}. {short_slash}"

    return full_label in normalized or short_slash in normalized or short_dot in normalized

def card_has_register_action(text: str) -> bool:
    normalized = normalize(text).lower()
    return "s'inscrire" in normalized or "s’inscrire" in normalized or "inscrire" in normalized


def card_is_already_registered(text: str) -> bool:
    """Vrai si la carte indique que l'utilisateur est deja inscrit (mot inscrit seul, ou bouton desinscrire)."""
    normalized = normalize(text).lower()
    return bool(re.search(r"\binscrit\b", normalized)) or "desinscrire" in normalized or "desinscription" in normalized


def is_already_registered_for_slot(page: Page, slot: str, settings: Settings, logger: logging.Logger) -> str | None:
    """Retourne le numero de court si l'utilisateur est deja inscrit sur ce creneau, None sinon."""
    time_re = slot_regex(slot)
    try:
        cards = page.locator(f'div.MuiPaper-root:has-text("{ACTIVITY}"):has-text("{slot}")')
        total = min(cards.count(), 100)
        for i in range(total):
            try:
                card = cards.nth(i)
                text = normalize(card.inner_text(timeout=500))
                if not text or "squash" not in text or not time_re.search(text) or not card_has_target_day(text, settings.target_date):
                    continue
                if card_is_already_registered(text):
                    court_match = re.search(r"\b([1-5])\s+court\s+squash\b", text, re.I)
                    court = court_match.group(1) if court_match else "?"
                    logger.info("Deja inscrit detecte pour le creneau %s / court %s", slot, court)
                    return court
            except Exception:
                continue
    except Exception as exc:
        logger.debug("Verification deja inscrit impossible pour %s : %s", slot, exc)
    return None


def find_best_card_for_slot(page: Page, slot: str, settings: Settings, logger: logging.Logger) -> tuple[Locator | None, str | None]:
    """
    Recherche orientée créneau :
    1. on scanne d'abord les cartes du créneau demandé, par exemple 18:10 ;
    2. on ne retient une carte que si elle contient explicitement "2 places restantes" ;
    3. on applique ensuite la priorité des courts : 3, 4, 5, 2, 1 ;
    4. si un court est vu mais sans 2 places restantes, on continue la recherche.
    """
    time_re = slot_regex(slot)
    expected = settings.expected_cards_per_slot

    configurable_wait(page, settings, logger, f"la recherche globale des cartes du créneau {slot}")

    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(settings.card_scroll_wait_ms)
    except Exception as exc:
        logger.debug("Scroll top impossible avant scan du créneau %s : %s", slot, exc)

    found_by_court: dict[str, Locator] = {}
    seen_courts: set[str] = set()
    seen_available_courts: set[str] = set()
    best_seen_count = 0
    stagnant_steps = 0

    for step in range(settings.card_load_max_scrolls + 1):
        try:
            scroll_y = page.evaluate("Math.round(window.scrollY)")
        except Exception:
            scroll_y = "?"

        try:
            # On démarre par l'heure, puis on valide le court et les places dans le texte de chaque carte.
            slot_cards = page.locator(
                f'div.MuiPaper-root:has-text("{ACTIVITY}"):has-text("{slot}")'
            )
            total = min(slot_cards.count(), 300)
            logger.debug(
                "Scan créneau %s étape %02d : %s cartes candidates, courts vus=%s, courts avec 2 places=%s, scrollY=%s",
                slot, step, total, sorted(seen_courts), sorted(seen_available_courts), scroll_y,
            )

            for i in range(total):
                try:
                    card = slot_cards.nth(i)
                    text = normalize(card.inner_text(timeout=900))
                    if not text:
                        continue
                    if "squash" not in text.lower() or not time_re.search(text):
                        continue

                    court_match = re.search(r"\b([1-5])\s+court\s+squash\b", text, re.I)
                    if not court_match:
                        logger.debug("Carte %s sans court détectable : %s", slot, text[:260])
                        continue

                    court = court_match.group(1)
                    seen_courts.add(court)

                    has_2_places = has_required_places(text)
                    has_register = card_has_register_action(text)

                    logger.debug(
                        "Carte vue %s / court %s : has_2_places=%s, has_register=%s, texte=%s",
                        slot, court, has_2_places, has_register, text[:320],
                    )

                    if not has_2_places:
                        logger.debug(
                            "Carte ignorée %s / court %s : elle n'indique pas exactement %s places restantes.",
                            slot, court, REQUIRED_REMAINING_PLACES,
                        )
                        continue

                    seen_available_courts.add(court)

                    if not has_register:
                        logger.debug(
                            "Carte ignorée %s / court %s : 2 places restantes mais bouton d'inscription non détecté.",
                            slot, court,
                        )
                        continue

                    # Sélecteur strict conforme au flux observé : activité + heure + court + 2 places restantes.
                    strict_locator = page.locator(
                        f'div.MuiPaper-root:has-text("{ACTIVITY}")'
                        f':has-text("{slot}")'
                        f':has-text("{court} Court Squash")'
                        f':has-text("{REQUIRED_REMAINING_PLACES} places restantes")'
                    )
                    try:
                        if strict_locator.count() > 0:
                            card = strict_locator.first
                    except Exception:
                        pass

                    if court not in found_by_court:
                        found_by_court[court] = card
                        logger.debug("Carte disponible mémorisée : %s / court %s", slot, court)
                except Exception as exc:
                    logger.debug("Lecture carte %s index %s impossible : %s", slot, i, exc)

        except Exception as exc:
            logger.debug("Scan MUI du créneau %s impossible étape %s : %s", slot, step, exc)

        for priority_court in COURT_PRIORITY:
            if priority_court in found_by_court:
                logger.info(
                    "Carte retenue pour %s : court %s avec %s places restantes",
                    slot, priority_court, REQUIRED_REMAINING_PLACES,
                )
                return found_by_court[priority_court], priority_court

        current_seen_count = len(seen_courts)
        if current_seen_count > best_seen_count:
            best_seen_count = current_seen_count
            stagnant_steps = 0
        else:
            stagnant_steps += 1

        if current_seen_count >= expected:
            logger.info(
                "Toutes les cartes attendues sont vues pour %s (%s courts), mais aucune n'a %s places restantes selon la priorité.",
                slot, current_seen_count, REQUIRED_REMAINING_PLACES,
            )
            break

        try:
            at_bottom = page.evaluate("window.innerHeight + window.scrollY >= document.body.scrollHeight - 5")
            if at_bottom and stagnant_steps >= 3:
                logger.debug(
                    "Bas de page atteint pour %s : courts vus=%s, courts avec 2 places=%s, mémorisés=%s",
                    slot, sorted(seen_courts), sorted(seen_available_courts), sorted(found_by_court.keys()),
                )
                break
        except Exception as exc:
            logger.debug("Détection bas de page impossible pendant scan %s : %s", slot, exc)

        logger.debug(
            "Scroll recherche cartes %s : deltaY=%s puis attente %sms",
            slot, settings.card_load_scroll_px, settings.card_scroll_wait_ms,
        )
        page.mouse.wheel(0, settings.card_load_scroll_px)
        page.wait_for_timeout(settings.card_scroll_wait_ms)

    logger.info(
        "Aucune carte disponible retenue pour %s. Courts vus=%s, courts avec 2 places=%s, courts mémorisés=%s",
        slot, sorted(seen_courts), sorted(seen_available_courts), sorted(found_by_court.keys()),
    )
    return None, None


def find_candidate_card(page: Page, slot: str, court: str, settings: Settings, logger: logging.Logger) -> Locator | None:
    """
    Compatibilité avec les versions précédentes : recherche ciblée d'un court.
    La logique principale utilise désormais find_best_card_for_slot().
    """
    card, selected_court = find_best_card_for_slot(page, slot, settings, logger)
    if selected_court == court:
        return card
    return None

def click_register_in_card(card: Locator, logger: logging.Logger) -> None:
    selectors = [
        "button:has-text(\"S'INSCRIRE\")",
        "button:has-text('INSCRIRE')",
        "[role='button']:has-text(\"S'INSCRIRE\")",
        "text=/S['’]INSCRIRE/i",
    ]
    for selector in selectors:
        try:
            btn = card.locator(selector)
            if btn.count() > 0 and btn.first.is_visible(timeout=1000):
                btn.first.click(timeout=5000)
                logger.debug("Bouton inscription cliqué via %s", selector)
                return
        except Exception:
            continue
    # Fallback : cliquer la carte elle-même.
    card.click(timeout=5000)


def select_time_filter_for_slot(page: Page, slot: str, settings: Settings, logger: logging.Logger) -> None:
    """Sélectionne le filtre horaire Resamania avant de scanner les cartes.

    Parcours demandé :
    - 18:10 -> combobox #mui-component-select-time -> option "Soir"
    - 17:30 -> combobox #mui-component-select-time -> option "L'après-midi"
    """
    label = TIME_FILTER_BY_SLOT.get(slot)
    if not label:
        logger.debug("Aucun filtre horaire configuré pour le créneau %s", slot)
        return

    logger.info("Sélection du filtre horaire pour %s : %s", slot, label)
    close_modal_if_present(page, logger)

    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    except Exception as exc:
        logger.debug("Scroll top impossible avant filtre horaire %s : %s", slot, exc)

    configurable_wait(page, settings, logger, f"la sélection du filtre horaire {label}")

    combo = page.locator("#mui-component-select-time")
    try:
        if combo.count() == 0:
            path = screenshot(page, f"time_combobox_not_found_{slot.replace(':','h')}")
            raise RuntimeError(f"Combobox horaire #mui-component-select-time introuvable. Screenshot : {path}")

        combo.first.click(timeout=7000)
        page.wait_for_timeout(500)
        logger.debug("Combobox horaire ouverte pour %s", slot)
    except Exception as exc:
        path = screenshot(page, f"time_combobox_click_failed_{slot.replace(':','h')}")
        raise RuntimeError(f"Impossible d'ouvrir la combobox horaire pour {slot}. Screenshot : {path}. Erreur : {exc}") from exc

    option_clicked = False
    option_attempts = [
        lambda: page.get_by_role("option", name=label),
        lambda: page.locator(f"[role='option']:has-text(\"{label}\")"),
        lambda: page.locator(f"li:has-text(\"{label}\")"),
        lambda: page.locator(f"text={label}"),
    ]

    for make_locator in option_attempts:
        try:
            option = make_locator()
            if option.count() > 0 and option.first.is_visible(timeout=2500):
                option.first.click(timeout=5000)
                option_clicked = True
                logger.debug("Option horaire sélectionnée pour %s : %s", slot, label)
                break
        except Exception as exc:
            logger.debug("Tentative option horaire ignorée pour %s/%s : %s", slot, label, exc)

    if not option_clicked:
        path = screenshot(page, f"time_option_not_found_{slot.replace(':','h')}")
        raise RuntimeError(f"Option horaire '{label}' introuvable pour {slot}. Screenshot : {path}")

    wait_dom(page)
    configurable_wait(page, settings, logger, f"le rechargement après filtre horaire {label}")
    close_modal_if_present(page, logger)

def reserve_slot(page: Page, slot: str, settings: Settings, logger: logging.Logger) -> bool:
    logger.info("Recherche du créneau %s", slot)
    close_modal_if_present(page, logger)

    # Revenir en haut de page pour que le scroll progressif reparte proprement.
    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    except Exception:
        pass

    # Si déjà inscrit sur ce créneau, pas besoin de réserver.
    if is_already_registered_for_slot(page, slot, settings, logger):
        logger.info("Créneau %s : déjà inscrit, aucune action nécessaire.", slot)
        return True

    logger.info("Début recherche par heure : %s. Ordre des courts : %s", slot, ", ".join(COURT_PRIORITY))

    candidate, court = find_best_card_for_slot(page, slot, settings, logger)
    if candidate is None or court is None:
        path = screenshot(page, f"no_court_available_{slot.replace(':','h')}")
        logger.error("Aucun court disponible pour %s. Screenshot : %s", slot, path)
        return False

    candidate.scroll_into_view_if_needed()
    page.wait_for_timeout(400)

    if settings.dry_run:
        logger.info("DRY RUN : carte trouvée, réservation NON confirmée pour %s / court %s", slot, court)
        screenshot(page, f"dry_run_found_{slot.replace(':','h')}_court_{court}")
        return True

    click_register_in_card(candidate, logger)
    wait_dom(page)
    configurable_wait(page, settings, logger, "l'ouverture de la modale d'inscription")

    # Dans la modale, Resamania affiche à nouveau S'INSCRIRE pour confirmer.
    if click_first_text(page, ["S'INSCRIRE", "S’INSCRIRE", "Confirmer", "Valider"], timeout_ms=7000):
        wait_dom(page)
        configurable_wait(page, settings, logger, "la confirmation d'inscription")
        logger.info("Réservation soumise : %s / court %s", slot, court)
        screenshot(page, f"submitted_{slot.replace(':','h')}_court_{court}")
        return True

    path = screenshot(page, f"confirm_not_found_{slot.replace(':','h')}_court_{court}")
    raise RuntimeError(f"Bouton de confirmation introuvable après sélection. Screenshot : {path}")



def build_planning_url(settings: Settings) -> str:
    """Construit l’URL directe du planning avec activité, date et plage horaire.

    Exemple attendu :
    https://member.resamania.com/larucheauxsports/planning?club=%2Flarucheauxsports%2Fclubs%2F2508&activity.activityGroups=%2Flarucheauxsports%2Factivity_groups%2F392&startedAt=2026-05-20&moment=17%3A00-19%3A00
    """
    query = urlencode(
        {
            "club": settings.club_path,
            "activity.activityGroups": settings.activity_group_path,
            "startedAt": settings.target_date.isoformat(),
            "moment": settings.moment,
        }
    )
    return f"{settings.planning_base_url}?{query}"


def open_direct_planning(page: Page, settings: Settings, logger: logging.Logger) -> None:
    """Ouvre directement le planning filtré au lieu de cliquer activité/date/plage horaire."""
    url = build_planning_url(settings)
    logger.info("Ouverture directe du planning filtré : date=%s, moment=%s", settings.target_date.isoformat(), settings.moment)
    logger.debug("URL planning directe : %s", url)
    page.goto(url, wait_until="domcontentloaded")
    wait_dom(page)
    configurable_wait(page, settings, logger, "le chargement du planning direct")
    close_modal_if_present(page, logger)

def run(settings: Settings, logger: logging.Logger) -> int:
    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=settings.headless, slow_mo=150 if settings.debug else 0)
        context: BrowserContext = browser.new_context(locale="fr-FR", viewport={"width": 1440, "height": 1100})
        page = context.new_page()
        page.set_default_timeout(15_000)

        try:
            login(page, settings, logger)

            if settings.use_direct_planning_url:
                open_direct_planning(page, settings, logger)
            else:
                open_booking_area(page, logger)
                select_activity(page, ACTIVITY, settings, logger)
                select_date(page, settings.target_date, settings, logger)

            results: dict[str, bool] = {}
            for slot in SLOTS:
                if not settings.use_direct_planning_url:
                    select_time_filter_for_slot(page, slot, settings, logger)
                results[slot] = reserve_slot(page, slot, settings, logger)

            # Vérification finale : contrôle des inscriptions sur tous les créneaux.
            logger.info("=== Vérification finale des inscriptions ===")
            for slot in SLOTS:
                court = is_already_registered_for_slot(page, slot, settings, logger)
                if court:
                    logger.info("Créneau %s : INSCRIT (court %s)", slot, court)
                else:
                    logger.info("Créneau %s : NON INSCRIT", slot)

            failed = [slot for slot, ok in results.items() if not ok]
            if failed:
                logger.error("Réservations incomplètes. Échecs : %s", ", ".join(failed))
                return 2
            logger.info("Toutes les réservations demandées sont traitées : %s", ", ".join(SLOTS))
            return 0
        except Exception as exc:
            logger.exception("Erreur : %s", exc)
            try:
                path = screenshot(page, "fatal_error")
                logger.error("Screenshot erreur : %s", path)
            except Exception:
                pass
            return 1
        finally:
            context.close()
            browser.close()


def main() -> int:
    args = parse_args()
    logger = setup_logger(args.debug)
    try:
        settings = load_settings(args)
    except Exception as exc:
        logger.error(str(exc))
        return 1

    logger.info(
        "Paramètres : target_date=%s, moment=%s, direct_planning=%s, headless=%s, dry_run=%s, debug=%s",
        settings.target_date.isoformat(),
        settings.moment,
        settings.use_direct_planning_url,
        settings.headless,
        settings.dry_run,
        settings.debug,
    )
    return run(settings, logger)


if __name__ == "__main__":
    raise SystemExit(main())
