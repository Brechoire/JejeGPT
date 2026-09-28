"""Partition intelligente : détection d'intention musicale, ABC → events/MusicXML.

V1 : une voix, pas de paroles (w: ignoré), pas de tuplets ni de
rythmes brisés (> <). Toute construction non supportée lève ValueError
avec un message clair plutôt que de produire un fichier faux.
"""

from __future__ import annotations

import re
import unicodedata
from fractions import Fraction

ABC_FENCE_RE = re.compile(r"```(?:abc|ABC)\s*\n(.*?)```", re.DOTALL)

# ---------------- Détection d'intention (français) ----------------

# Veto absolus : ces contextes ne sont jamais musicaux.
_VETO_RES = [
    r"disque(?:\s*dur)?", r"\bssd\b", r"sauvegarde", r"format(?:age|er)?\s",
    r"note de (?:frais|service)", r"notes de (?:frais|réunion|reunion)",
    r"prend(?:re|re des|s des) notes", r"bloc-?notes?",
    r"jouer? un r[ôo]le", r"joue un r[ôo]le",
    r"gamme de (?:prix|produits?)", r"\btarif", r"\bprix",
]
_VETO_RE = re.compile("|".join(_VETO_RES))

# Forts signaux musicaux (2 pts : déclenchent seuls, seuil = 2).
_STRONG_RES = [
    r"\bpartition\b", r"\bpartoche\b", r"solf[èe]ge", r"tablature",
    r"```abc", r"format abc\b", r"\ben abc\b",
    r"(?:^|\s)\d+/\d+\b",  # signature rythmique 4/4, 3/4…
    r"joue-?moi", r"\bjou?ez\b",
    r"(?:^|[\s,])(?:do|r[ée]|mi|fa|sol|la|si)(?:[\s,]+(?:do|r[ée]|mi|fa|sol|la|si)){2,}",
    r"\b[C-GA-B](?:[\s,]+[C-GA-B]){2,}",
    r"compos\w*\s+(?:une? |la |le )?(?:musique|m[ée]lodie|chanson|morceau)",
    r"\baccords?\b",
]
_STRONG_RE = re.compile("|".join(_STRONG_RES))

# Signaux faibles (1 pt) : ne déclenchent qu'en combinaison.
_WEAK_RES = [
    r"tonalit[ée]", r"\btempo\b", r"\bbpm\b",
    r"m[ée]lodie", r"\brefrain\b", r"\bcouplet\b",
    r"\ben\s+(?:do|r[ée]|mi|fa|sol|la|si)\b", r"\bmineur\b", r"\bmajeur\b",
    r"\bpiano\b", r"\bguitare\b", r"\bviolon\b", r"\baccord[ée]on\b",
    r"\bfl[ûu]te\b", r"\bbatterie\b", r"\bsaxo",
    r"\bchant(?:er|e|eur)?\b",
    r"gamme (?:de |du )?(?:do|r[ée]|mi|fa|sol|la|si)",
]
_WEAK_RE = re.compile("|".join(_WEAK_RES))

_PARTITION_CMD = "/partition"
_NO_SCORE_CMD = "/sans-partition"

_FRENCH_KEYS = {
    "do": "C", "ré": "D", "re": "D", "mi": "E", "fa": "F",
    "sol": "G", "la": "A", "si": "B",
}
_KEY_RE = re.compile(r"\ben\s+(do|r[ée]|mi|fa|sol|la|si)\b", re.IGNORECASE)
_TEMPO_RE = re.compile(r"(\d{2,3})\s*bpm")
_METER_RE = re.compile(r"(\d/\d)")


def _norm(text: str) -> str:
    ascii_ = "".join(
        c for c in unicodedata.normalize("NFD", (text or "").lower())
        if unicodedata.category(c) != "Mn"
    )
    # "d'accord" contient "accord" : on le neutralise avant scoring.
    return re.sub(r"\bd['\u2019]accord\b", "ok", ascii_)


def _strip_command(text: str, cmd: str) -> str:
    return re.sub(rf"^{re.escape(cmd)}\b\s*", "", (text or "").strip(), flags=re.IGNORECASE)


def parse_partition_command(text: str) -> dict:
    """Extrait titre / tonalité / tempo d'une commande /partition.

    Heuristiques FR légères : « en Do », « 60 BPM », « 4/4 ». Le reste
    nettoyé devient le titre. Jamais d'erreur : défauts vides/zéro.
    """
    rest = _strip_command(text, _PARTITION_CMD)
    norm = _norm(rest)
    key = ""
    if match := _KEY_RE.search(norm):
        key = _FRENCH_KEYS.get(match.group(1).replace("é", "e"), "")
    tempo = int(match.group(1)) if (match := _TEMPO_RE.search(norm)) else 0
    meter = match.group(1) if (match := _METER_RE.search(rest)) else ""
    title = _KEY_RE.sub(" ", rest)
    title = _TEMPO_RE.sub(" ", title)
    title = _METER_RE.sub(" ", title)
    title = re.sub(r"\s+", " ", title).strip(" ,.-")
    return {"source": "command", "title": title, "key": key, "tempo": tempo, "meter": meter}


def detect_score_request(text: str) -> dict | None:
    """Détecte une demande de partition. None si non pertinent.

    Priorités : /sans-partition (veto) > /partition (force) > veto
    contextuels > scoring (seuil 2, conservateur : le doute n'injecte pas).
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.lower().startswith(_NO_SCORE_CMD):
        return None
    if raw.lower().startswith(_PARTITION_CMD):
        return parse_partition_command(raw)
    norm = _norm(raw)
    if _VETO_RE.search(norm):
        return None
    score = 2 * len(_STRONG_RE.findall(norm)) + len(_WEAK_RE.findall(norm))
    if score < 2:
        return None
    return {"source": "auto", "title": "", "key": "", "tempo": 0, "meter": ""}


def score_system_instruction(detection: dict) -> str:
    """Consigne injectée dans l'appel LLM (message stocké inchangé)."""
    hints = []
    if detection.get("title"):
        hints.append(f"Titre : {detection['title']}.")
    if detection.get("key"):
        hints.append(f"Tonalité : {detection['key']}.")
    if detection.get("tempo"):
        hints.append(f"Tempo : {detection['tempo']} BPM.")
    if detection.get("meter"):
        hints.append(f"Signature : {detection['meter']}.")
    framing = " " + " ".join(hints) if hints else ""
    return (
        "L'utilisateur veut une partition de musique." + framing +
        " Réponds normalement PUIS ajoute un bloc ```abc complet et jouable "
        "(mélodie entière, une voix) avec en-têtes X:, T:, M:, L:, Q:, K:. "
        "Termine par 2-3 phrases expliquant tes choix (tonalité, tempo, structure)."
    )


def score_extra_message(user_text: str) -> dict | None:
    """Message system à ajouter à l'historique, ou None si non pertinent."""
    detection = detect_score_request(user_text)
    if detection is None:
        return None
    return {"role": "system", "content": score_system_instruction(detection)}


def extract_abc_blocks(markdown_text: str) -> list[str]:
    """Blocs ```abc d'une réponse, nettoyés."""
    return [b.strip() for b in ABC_FENCE_RE.findall(markdown_text or "") if b.strip()]


# ---------------- Parse ABC (une voix) ----------------

LETTER_SEMI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
LETTER_FR = {"C": "Do", "D": "Ré", "E": "Mi", "F": "Fa", "G": "Sol", "A": "La", "B": "Si"}

MAJOR_FIFTHS = {
    "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
    "F": -1, "Bb": -2, "Eb": -3, "Ab": -4, "Db": -5, "Gb": -6, "Cb": -7,
}
MINOR_FIFTHS = {
    "Am": 0, "Em": 1, "Bm": 2, "F#m": 3, "C#m": 4, "G#m": 5, "D#m": 6, "A#m": 7,
    "Dm": -1, "Gm": -2, "Cm": -3, "Fm": -4, "Bbm": -5, "Ebm": -6,
}
_SHARP_ORDER = ["F", "C", "G", "D", "A", "E", "B"]
_FLAT_ORDER = ["B", "E", "A", "D", "G", "C", "F"]

_HEADER_RE = re.compile(r"^([A-Za-z]):(.*)$")
_TEMPO_RE2 = re.compile(r"(\d+)\s*/\s*(\d+)\s*=\s*(\d+)")
_TEMPO_NUM = re.compile(r"^\s*(\d{2,3})\s*$")
_NOTE_RE = re.compile(r"(?P<acc>\^+|_+|=)?(?P<letter>[A-Ga-g])(?P<oct>[,']*)(?P<len>\d*/*\d*)(?P<broken>[<>]?)")
_REST_RE = re.compile(r"[zZxX](?P<len>\d*/*\d*)")
_LEN_RE = re.compile(r"(?P<num>\d+)?(?P<slash>/+)?(?P<den>\d+)?")

TYPE_BY_QUARTERS = {
    Fraction(4, 1): "whole", Fraction(2, 1): "half", Fraction(1, 1): "quarter",
    Fraction(1, 2): "eighth", Fraction(1, 4): "16th", Fraction(1, 8): "32nd",
    Fraction(1, 16): "64th",
}


def _parse_key(raw: str) -> tuple[int, str]:
    """Retourne (fifths, mode). Inconnu → Do majeur (jamais d'erreur)."""
    token = (raw or "").strip().split()[0] if (raw or "").strip() else ""
    token = token.replace("min", "m").replace("maj", "").replace("major", "").replace("minor", "m")
    if token in MAJOR_FIFTHS:
        return MAJOR_FIFTHS[token], "major"
    if token in MINOR_FIFTHS:
        return MINOR_FIFTHS[token], "minor"
    return 0, "major"


def _key_alters(fifths: int, mode: str) -> dict[str, int]:
    """Altération par lettre imposée par l'armure (approximation V1)."""
    alters: dict[str, int] = {}
    if mode == "major":
        scale = {0, 2, 4, 5, 7, 9, 11}
    else:
        scale = {0, 2, 3, 5, 7, 8, 10}
    sharp = fifths >= 0
    order = _SHARP_ORDER if sharp else _FLAT_ORDER
    signed = set()
    for i in range(abs(fifths)):
        signed.add(order[i])
    for letter, semi in LETTER_SEMI.items():
        if semi in scale:
            alters[letter] = 0
        elif letter in signed:
            alters[letter] = 1 if sharp else -1
        else:
            alters[letter] = 0
    return alters


def _parse_length(raw: str, default: Fraction) -> Fraction:
    match = _LEN_RE.fullmatch(raw or "")
    if match is None:
        raise ValueError(f"Durée illisible : {raw!r}.")
    num = int(match.group("num")) if match.group("num") else 1
    den: Fraction = Fraction(1, 1)
    if match.group("slash"):
        slashes = len(match.group("slash"))
        den = Fraction(int(match.group("den")) if match.group("den") else 2 ** slashes, 1)
    return default * num / den


def _parse_meter(raw: str) -> tuple[str, str] | None:
    token = (raw or "").strip()
    if token in ("C", "c"):
        return "4", "4"
    if token in ("C|", "c|"):
        return "2", "2"
    if token.lower() in ("none", "free"):
        return None
    if match := re.fullmatch(r"(\d+)\s*/\s*(\d+)", token):
        return match.group(1), match.group(2)
    return "4", "4"


def _parse_tempo(raw: str, default_len_q: Fraction) -> tuple[Fraction, int]:
    """Retourne (valeur de battement en noires, BPM). Défaut : noire = 100."""
    if match := _TEMPO_RE2.search(raw or ""):
        beat = Fraction(int(match.group(1)), int(match.group(2))) * 4
        return beat, int(match.group(3))
    if match := _TEMPO_NUM.match(raw or ""):
        return default_len_q, int(match.group(1))
    return Fraction(1, 1), 100


def _split_lines(abc: str) -> tuple[dict, list[str]]:
    """En-têtes (X,T,M,L,Q,K) + lignes musicales (champs inconnus et w: ignorés)."""
    headers: dict = {}
    music: list[str] = []
    for line in (abc or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("%"):
            continue
        if match := _HEADER_RE.match(stripped):
            field, value = match.group(1), match.group(2).strip()
            if field in "XTMLQK" and field not in headers:
                headers[field] = value
            continue
        music.append(stripped)
    return headers, music


def _tokenize_note(token: str, default_len: Fraction) -> dict:
    match = _NOTE_RE.fullmatch(token)
    if match is None:
        raise ValueError(f"Note illisible : {token!r}. Exemple valide : C D E F | G A B c |.")
    if match.group("broken"):
        raise ValueError(
            "Rythmes brisés (> et <) non supportés en V1. "
            "Remplacez-les par des durées explicites (ex. 3/2)."
        )
    letter = match.group("letter").upper()
    octave = 4 + (1 if match.group("letter").islower() else 0)
    octave -= match.group("oct").count(",")
    octave += match.group("oct").count("'")
    return {
        "letter": letter, "octave": octave,
        "acc": match.group("acc") or "",
        "dur": _parse_length(match.group("len"), default_len),
    }


def _parse_body(body: str, default_len: Fraction) -> list[list[dict]]:
    """Découpe en mesures : listes d'événements (notes / silences / accords)."""
    measures: list[list[dict]] = [[]]
    pos = 0
    length = len(body)
    while pos < length:
        char = body[pos]
        if char.isspace():
            pos += 1
            continue
        if char == "|":
            measures.append([])
            while pos < length and body[pos] in "|:[]":
                pos += 1
            continue
        if char == "[":
            end = body.find("]", pos)
            if end < 0:
                raise ValueError("Crochet [ non refermé.")
            inner = body[pos + 1:end]
            pos = end + 1
            if re.match(r"^[A-Z]:", inner) or re.fullmatch(r"\d+", inner.strip()):
                continue  # champ inline ou chiffre de reprise : ignoré
            parts = re.findall(r"\^+|_+|=|[A-Ga-g][,']*(?:\d*/*\d*)?", inner)
            if not parts or not any(re.match(r"[A-Ga-g]", p) for p in parts):
                continue
            for index, part in enumerate([p for p in parts if re.match(r"[A-Ga-g]", p)]):
                note = _tokenize_note(part, default_len)
                note["chord"] = index > 0
                measures[-1].append(note)
            continue
        if char == "(":
            raise ValueError("Tuplets non supportés en V1. Écrivez les notes en durées explicites.")
        if char in "zZxX":
            match = _REST_RE.match(body[pos:])
            assert match is not None
            measures[-1].append({"rest": True, "dur": _parse_length(match.group("len"), default_len)})
            pos += match.end()
            continue
        if char in "~":
            pos += 1
            continue
        if char == "!":
            end = body.find("!", pos + 1)
            pos = end + 1 if end >= 0 else length
            continue
        match = _NOTE_RE.match(body[pos:])
        if match is None:
            raise ValueError(f"Symbole illisible : {body[pos:pos+8]!r}. Exemple valide : C D E F | G A B c |.")
        measures[-1].append(_tokenize_note(match.group(0), default_len))
        pos += match.end()
    return [m for m in measures if m]


def parse_abc(abc: str) -> dict:
    """Parse un bloc ABC en structure (en-têtes, mesures, tempo). Lève ValueError claire."""
    text = (abc or "").strip()
    if not text:
        raise ValueError("Bloc ABC vide. Exemple valide :\nX:1\nT:Exemple\nM:4/4\nL:1/4\nK:C\nC D E F | G A B c |")
    headers, lines = _split_lines(text)
    default_len = Fraction(1, 8)
    if "L" in headers:
        try:
            num, _, den = headers["L"].partition("/")
            default_len = Fraction(int(num), int(den))
        except (ValueError, ZeroDivisionError):
            raise ValueError(f"En-tête L: illisible : {headers['L']!r} (ex. L:1/8).") from None
    body = " ".join(lines)
    measures = _parse_body(body, default_len)
    if not measures or not any("letter" in ev or "rest" in ev for m in measures for ev in m):
        raise ValueError("Aucune note trouvée. Exemple valide :\nK:C\nC D E F | G A B c |")
    fifths, mode = _parse_key(headers.get("K", "C"))
    alters = _key_alters(fifths, mode)
    beat_q, bpm = _parse_tempo(headers.get("Q", ""), default_len * 4)
    return {
        "title": headers.get("T", "Partition"),
        "meter": _parse_meter(headers.get("M", "4/4")),
        "fifths": fifths, "mode": mode,
        "tempo_bpm": bpm, "beat_quarters": beat_q,
        "measures": measures, "key_alters": alters,
    }


def _spell(note: dict, alters: dict[str, int]) -> tuple[str, int, int]:
    """Retourne (degré, altération, octave) pour MusicXML."""
    letter = note["letter"]
    acc = note["acc"]
    if acc.startswith("^^"):
        alter = 2
    elif acc.startswith("__"):
        alter = -2
    elif acc.startswith("^"):
        alter = 1
    elif acc.startswith("_"):
        alter = -1
    elif acc.startswith("="):
        alter = 0
    else:
        alter = alters.get(letter, 0)
    return letter, alter, note["octave"]


def _midi(letter: str, alter: int, octave: int) -> int:
    return 12 * (octave + 1) + LETTER_SEMI[letter] + alter


def _french_name(letter: str, alter: int) -> str:
    name = LETTER_FR[letter]
    if alter > 0:
        return name + "♯" * alter
    if alter < 0:
        return name + "♭" * (-alter)
    return name


def abc_to_events(abc: str) -> dict:
    """Convertit en événements jouables (temps en noires). Base de l'audio et du mode débutant."""
    parsed = parse_abc(abc)
    events: list[dict] = []
    cursor = Fraction(0, 1)
    for measure in parsed["measures"]:
        for ev in measure:
            dur_q = ev["dur"] * 4
            if "rest" in ev:
                cursor += dur_q
                continue
            letter, alter, octave = _spell(ev, parsed["key_alters"])
            start = cursor
            if not ev.get("chord"):
                cursor += dur_q
            events.append({
                "midi": _midi(letter, alter, octave),
                "name": _french_name(letter, alter),
                "start": float(start), "dur": float(dur_q),
            })
    return {
        "title": parsed["title"], "tempo_bpm": parsed["tempo_bpm"],
        "beat_quarters": float(parsed["beat_quarters"]),
        "events": events,
    }


def _type_and_dot(dur_q: Fraction) -> tuple[str, bool] | tuple[None, bool]:
    if dur_q in TYPE_BY_QUARTERS:
        return TYPE_BY_QUARTERS[dur_q], False
    for base, name in TYPE_BY_QUARTERS.items():
        if dur_q == base * Fraction(3, 2):
            return name, True
    return None, False


def _xml_escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )


def abc_to_musicxml(abc: str, title: str = "") -> str:
    """Convertit un bloc ABC en MusicXML 4.0 (une voix, clé de sol)."""
    parsed = parse_abc(abc)
    name = _xml_escape(title or parsed["title"])
    divisions = 480
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
        '"http://www.musicxml.org/dtds/partwise.dtd">',
        '<score-partwise version="4.0">',
        f"<part-list><score-part id=\"P1\"><part-name>{name}</part-name></score-part></part-list>",
        '<part id="P1">',
    ]
    for number, measure in enumerate(parsed["measures"], start=1):
        out.append(f"<measure number=\"{number}\">")
        if number == 1:
            out.append(f"<attributes><divisions>{divisions}</divisions>")
            out.append(
                f"<key><fifths>{parsed['fifths']}</fifths><mode>{parsed['mode']}</mode></key>"
            )
            if parsed["meter"] is not None:
                beats, beat_type = parsed["meter"]
                out.append(f"<time><beats>{beats}</beats><beat-type>{beat_type}</beat-type></time>")
            out.append('<clef><sign>G</sign><line>2</line></clef></attributes>')
            out.append(f"<sound tempo=\"{parsed['tempo_bpm']}\"/>")
        for ev in measure:
            dur_q = ev["dur"] * 4
            ticks = int(dur_q * divisions)
            if "rest" in ev:
                out.append(f"<note><rest/><duration>{ticks}</duration><voice>1</voice></note>")
                continue
            letter, alter, octave = _spell(ev, parsed["key_alters"])
            parts = ["<note>"]
            if ev.get("chord"):
                parts.append("<chord/>")
            parts.append(
                f"<pitch><step>{letter}</step><alter>{alter}</alter><octave>{octave}</octave></pitch>"
            )
            parts.append(f"<duration>{ticks}</duration><voice>1</voice>")
            note_type, dotted = _type_and_dot(dur_q)
            if note_type:
                parts.append(f"<type>{note_type}</type>")
                if dotted:
                    parts.append("<dot/>")
            parts.append("</note>")
            out.append("".join(parts))
        if number == len(parsed["measures"]):
            out.append("<barline location=\"right\"><bar-style>light-heavy</bar-style></barline>")
        out.append("</measure>")
    out.append("</part></score-partwise>")
    return "\n".join(out) + "\n"


SCORE_DEMOS: list[dict] = [
    {
        "title": "Frère Jacques",
        "abc": "X:1\nT:Frère Jacques\nM:4/4\nL:1/4\nQ:1/4=100\nK:C\n"
               "C D E C|C D E C|E F G2|E F G2|G A G F E C|G A G F E C|C G, C2|C G, C2|]",
    },
    {
        "title": "Joyeux anniversaire",
        "abc": "X:1\nT:Joyeux anniversaire\nM:3/4\nL:1/8\nQ:1/4=110\nK:F\n"
               "C C|D C F|E2 C C|D C G|F2 C C|c A F|E2 D D|B, D F|E2 C C|D C G|F2|]",
    },
    {
        "title": "Gamme de Do majeur",
        "abc": "X:1\nT:Gamme de Do majeur\nM:4/4\nL:1/4\nQ:1/4=80\nK:C\n"
               "C D E F|G A B c|c B A G|F E D C|]",
    },
]
