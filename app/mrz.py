"""Machine Readable Zone (MRZ) parsing per ICAO Doc 9303.

Supported layouts:
    TD1 - ID cards:        3 lines x 30 chars
    TD2 - older ID/visas:  2 lines x 36 chars
    TD3 - passports:       2 lines x 44 chars

Each important field is protected by a check digit (weights 7,3,1 repeating;
0-9 -> 0-9, A-Z -> 10-35, '<' -> 0), plus a composite check digit over the
whole data line(s). OCR typically confuses O/0, I/1, S/5, B/8 etc., so numeric
fields are normalised before validation, and alphanumeric fields that fail
their check digit are retried with ambiguous characters swapped.
"""

import itertools
import re
from dataclasses import dataclass, field
from datetime import date

MRZ_CHARS = re.compile(r"^[A-Z0-9<]+$")

_TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "G": "6", "B": "8"})
_TO_ALPHA = str.maketrans({"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B"})
# Characters OCR commonly swaps inside alphanumeric fields (document numbers).
_AMBIGUOUS = {"0": "O", "O": "0", "1": "I", "I": "1", "5": "S", "S": "5", "8": "B", "B": "8", "2": "Z", "Z": "2"}

LAYOUTS = {"TD1": (3, 30), "TD2": (2, 36), "TD3": (2, 44)}


class MRZParseError(ValueError):
    """Raised when text cannot be interpreted as an MRZ."""


def _char_value(char: str) -> int:
    if char.isdigit():
        return int(char)
    if "A" <= char <= "Z":
        return ord(char) - ord("A") + 10
    return 0  # '<' filler


def check_digit(data: str) -> str:
    weights = (7, 3, 1)
    total = sum(_char_value(c) * weights[i % 3] for i, c in enumerate(data))
    return str(total % 10)


def _is_valid(data: str, digit: str) -> bool:
    return check_digit(data) == digit


def _numeric(text: str) -> str:
    return text.translate(_TO_DIGIT)


def _alpha(text: str) -> str:
    return text.translate(_TO_ALPHA)


def _repair_alnum(data: str, digit: str, max_ambiguous: int = 6) -> str:
    """If `data` fails its check digit, try swapping OCR-ambiguous characters until it passes.

    A mod-10 check digit can be satisfied by more than one guess, so we take the
    fewest swaps and, among those, the candidate with the most digits (document
    and personal numbers are digit-heavy).
    """
    if _is_valid(data, digit):
        return data
    positions = [i for i, c in enumerate(data) if c in _AMBIGUOUS][:max_ambiguous]
    for count in range(1, len(positions) + 1):
        candidates = []
        for combo in itertools.combinations(positions, count):
            chars = list(data)
            for i in combo:
                chars[i] = _AMBIGUOUS[chars[i]]
            candidate = "".join(chars)
            if _is_valid(candidate, digit):
                candidates.append(candidate)
        if candidates:
            return max(candidates, key=lambda c: sum(ch.isdigit() for ch in c))
    return data


def _parse_date(yymmdd: str, is_birth_date: bool) -> str | None:
    if not yymmdd.isdigit():
        return None
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    current_yy = date.today().year % 100
    if is_birth_date:
        century = 2000 if yy <= current_yy else 1900
    else:
        # Expiry dates are almost always in this century; very old cards (70-99) are 19xx.
        century = 1900 if yy >= 70 else 2000
    try:
        return date(century + yy, mm, dd).isoformat()
    except ValueError:
        return None


_K_IN_FILLER = re.compile(r"(?<=<)K(?=[<K]|$)")


def _fix_filler_misreads(text: str) -> str:
    """OCR often reads the '<' filler as 'K'. A K that follows a '<' and precedes '<', 'K'
    or the end of the field is filler, not a name initial (e.g. 'SMITH<<JOHN<KKKK')."""
    previous = None
    while previous != text:
        previous, text = text, _K_IN_FILLER.sub("<", text)
    return text


def _parse_names(text: str) -> tuple[str, str]:
    text = _fix_filler_misreads(_alpha(text)).rstrip("<")
    surname, _, given = text.partition("<<")
    clean = lambda s: re.sub(r"<+", " ", s).strip()
    return clean(surname), clean(given)


def _clean_code(text: str) -> str:
    return _alpha(text).replace("<", "")


def _sex(char: str) -> str:
    return {"M": "M", "F": "F"}.get(char, "X")


@dataclass
class MRZResult:
    format: str
    document_type: str
    issuing_country: str
    surname: str
    given_names: str
    document_number: str
    nationality: str
    date_of_birth: str | None
    sex: str
    expiry_date: str | None
    optional_data: str
    raw_lines: list[str]
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return all(self.checks.values())

    @property
    def check_score(self) -> int:
        return sum(self.checks.values())


def normalize_line(line: str) -> str:
    line = line.upper().replace(" ", "").replace("«", "<<")
    return re.sub(r"[^A-Z0-9<]", "<", line)


def _fit_length(line: str, length: int) -> str:
    """Pad a slightly short OCR line with fillers, or trim surplus trailing fillers/characters."""
    if len(line) < length:
        return line + "<" * (length - len(line))
    return line[:length]


def detect_layout(lines: list[str]) -> str:
    if len(lines) == 3:
        return "TD1"
    if len(lines) == 2:
        # OCR often drops trailing '<' fillers from the name line, but the data line
        # ends in a check digit and keeps its length, so judge by the longest line.
        longest = max(len(l) for l in lines)
        return "TD3" if abs(longest - 44) <= abs(longest - 36) else "TD2"
    raise MRZParseError(f"Expected 2 or 3 MRZ lines, got {len(lines)}.")


def parse_mrz(lines: list[str]) -> MRZResult:
    lines = [normalize_line(l) for l in lines if l.strip()]
    layout = detect_layout(lines)
    _, length = LAYOUTS[layout]
    lines = [_fit_length(l, length) for l in lines]
    if layout == "TD1":
        return _parse_td1(lines)
    return _parse_td2_td3(lines, layout)


def _parse_td2_td3(lines: list[str], layout: str) -> MRZResult:
    l1, l2 = lines
    surname, given = _parse_names(l1[5:])

    doc_cd = _numeric(l2[9])
    doc_number = _repair_alnum(l2[0:9], doc_cd)
    dob, dob_cd = _numeric(l2[13:19]), _numeric(l2[19])
    expiry, expiry_cd = _numeric(l2[21:27]), _numeric(l2[27])

    if layout == "TD3":
        optional, optional_cd = l2[28:42], _numeric(l2[42])
        if optional_cd.isdigit():
            optional = _repair_alnum(optional, optional_cd)
        composite_cd = _numeric(l2[43])
        composite_data = doc_number + doc_cd + dob + dob_cd + expiry + expiry_cd + optional + optional_cd
    else:
        optional, optional_cd = l2[28:35], None
        composite_cd = _numeric(l2[35])
        composite_data = doc_number + doc_cd + dob + dob_cd + expiry + expiry_cd + optional

    checks = {
        "document_number": _is_valid(doc_number, doc_cd),
        "date_of_birth": _is_valid(dob, dob_cd),
        "expiry_date": _is_valid(expiry, expiry_cd),
        "composite": _is_valid(composite_data, composite_cd),
    }
    # TD3 personal-number check digit may be '<' when the field is empty.
    if optional_cd is not None and not (optional_cd == "<" or (optional.strip("<") == "" and optional_cd in "0<")):
        checks["optional_data"] = _is_valid(optional, optional_cd)

    return MRZResult(
        format=layout,
        document_type=_clean_code(l1[0:2]),
        issuing_country=_clean_code(l1[2:5]),
        surname=surname,
        given_names=given,
        document_number=doc_number.replace("<", ""),
        nationality=_clean_code(l2[10:13]),
        date_of_birth=_parse_date(dob, is_birth_date=True),
        sex=_sex(l2[20]),
        expiry_date=_parse_date(expiry, is_birth_date=False),
        optional_data=optional.replace("<", " ").strip(),
        raw_lines=[l1, l2],
        checks=checks,
    )


def _parse_td1(lines: list[str]) -> MRZResult:
    l1, l2, l3 = lines
    surname, given = _parse_names(l3)

    doc_number, doc_cd = l1[5:14], l1[14]
    optional1 = l1[15:30]
    if doc_cd == "<":
        # Long document number: continues in optional data, last char before '<' is its check digit.
        extension = optional1.split("<", 1)[0]
        doc_number, doc_cd = doc_number + extension[:-1], extension[-1:] or "<"
        optional1 = optional1[len(extension):]
    doc_cd = _numeric(doc_cd)
    doc_number = _repair_alnum(doc_number, doc_cd)

    dob, dob_cd = _numeric(l2[0:6]), _numeric(l2[6])
    expiry, expiry_cd = _numeric(l2[8:14]), _numeric(l2[14])
    optional2 = l2[18:29]
    composite_cd = _numeric(l2[29])
    composite_data = l1[5:30] + dob + dob_cd + expiry + expiry_cd + optional2

    checks = {
        "document_number": _is_valid(doc_number, doc_cd),
        "date_of_birth": _is_valid(dob, dob_cd),
        "expiry_date": _is_valid(expiry, expiry_cd),
        "composite": _is_valid(composite_data, composite_cd),
    }

    optional = " ".join(p for p in (optional1.replace("<", " ").strip(), optional2.replace("<", " ").strip()) if p)
    return MRZResult(
        format="TD1",
        document_type=_clean_code(l1[0:2]),
        issuing_country=_clean_code(l1[2:5]),
        surname=surname,
        given_names=given,
        document_number=doc_number.replace("<", ""),
        nationality=_clean_code(l2[15:18]),
        date_of_birth=_parse_date(dob, is_birth_date=True),
        sex=_sex(l2[7]),
        expiry_date=_parse_date(expiry, is_birth_date=False),
        optional_data=optional,
        raw_lines=[l1, l2, l3],
        checks=checks,
    )
