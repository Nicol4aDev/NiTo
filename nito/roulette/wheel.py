"""Ruota, numeri e categorie di puntata (colore, pari/dispari, metà, dozzine, colonne).

I numeri sono interi 0-36; il "00" della roulette americana è DOUBLE_ZERO (37).
Lo zero (e il doppio zero) è solo "verde": interrompe le serie di tutte le
altre categorie, come al tavolo.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

DOUBLE_ZERO = 37

RED = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36})


class Wheel(str, Enum):
    EUROPEAN = "european"  # 0-36
    AMERICAN = "american"  # 0, 00, 1-36

    @property
    def pockets(self) -> range:
        return range(38) if self is Wheel.AMERICAN else range(37)


def is_zero(n: int) -> bool:
    return n in (0, DOUBLE_ZERO)


def _plain(pred: Callable[[int], bool]) -> Callable[[int], bool]:
    """Vale solo per i numeri 1-36: lo zero non è né pari, né basso, né in una dozzina."""
    return lambda n: not is_zero(n) and pred(n)


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    emoji: str
    group: str
    test: Callable[[int], bool]

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.name}" if self.emoji else self.name


_ALL = [
    Category("rosso", "Rosso", "🔴", "colore", lambda n: n in RED),
    Category("nero", "Nero", "⚫", "colore", _plain(lambda n: n not in RED)),
    Category("verde", "Verde", "🟢", "colore", is_zero),
    Category("pari", "Pari", "", "parità", _plain(lambda n: n % 2 == 0)),
    Category("dispari", "Dispari", "", "parità", _plain(lambda n: n % 2 == 1)),
    Category("basso", "1-18", "", "metà", _plain(lambda n: n <= 18)),
    Category("alto", "19-36", "", "metà", _plain(lambda n: n >= 19)),
]
for _i in range(3):
    _ALL.append(
        Category(f"d{_i + 1}", f"{_i + 1}ª dozzina", "", "dozzina",
                 _plain(lambda n, i=_i: (n - 1) // 12 == i))
    )
for _i in range(3):
    _ALL.append(
        Category(f"c{_i + 1}", f"{_i + 1}ª colonna", "", "colonna",
                 _plain(lambda n, i=_i: (n - 1) % 3 == i))
    )

CATEGORIES: dict[str, Category] = {c.key: c for c in _ALL}

GROUPS: dict[str, list[str]] = {}
for _c in _ALL:
    GROUPS.setdefault(_c.group, []).append(_c.key)

_ALIASES = {
    "rosso": ("rossi", "red", "🔴"),
    "nero": ("neri", "black", "⚫"),
    "verde": ("verdi", "green", "zero", "🟢"),
    "pari": ("even",),
    "dispari": ("odd",),
    "basso": ("bassi", "manque", "low", "1-18"),
    "alto": ("alti", "passe", "high", "19-36"),
    "d1": ("dozzina1", "1-12"),
    "d2": ("dozzina2", "13-24"),
    "d3": ("dozzina3", "25-36"),
    "c1": ("colonna1", "col1"),
    "c2": ("colonna2", "col2"),
    "c3": ("colonna3", "col3"),
}
_LOOKUP = {key: key for key in CATEGORIES}
for _key, _names in _ALIASES.items():
    for _name in _names:
        _LOOKUP[_name] = _key


def find_category(text: str) -> Category | None:
    key = _LOOKUP.get(text.strip().lower().replace("️", ""))
    return CATEGORIES[key] if key else None


def categories_of(n: int) -> frozenset[str]:
    return frozenset(c.key for c in _ALL if c.test(n))


def pocket_str(n: int) -> str:
    return "00" if n == DOUBLE_ZERO else str(n)


def color_emoji(n: int) -> str:
    if is_zero(n):
        return "🟢"
    return "🔴" if n in RED else "⚫"


def fmt_pocket(n: int) -> str:
    return f"{color_emoji(n)}{pocket_str(n)}"


def parse_pocket(text: str, wheel: Wheel) -> int:
    """Converte "17", "0" o "00" (solo ruota americana) in numero; ValueError se non valido."""
    text = str(text).strip()
    if text == "00":
        if wheel is not Wheel.AMERICAN:
            raise ValueError("il 00 esiste solo sulla roulette americana")
        return DOUBLE_ZERO
    if not text.isdigit():
        raise ValueError(f"«{text}» non è un numero della roulette")
    n = int(text)
    if not 0 <= n <= 36:
        raise ValueError(f"{n} fuori dalla ruota (0-36)")
    return n


# --- serie ---
# Le sequenze sono in ordine cronologico: l'ultimo elemento è il giro più recente.


def current_streak(numbers: Sequence[int], key: str) -> int:
    """Quante volte di fila è uscita la categoria negli ultimi giri."""
    test = CATEGORIES[key].test
    count = 0
    for n in reversed(numbers):
        if not test(n):
            break
        count += 1
    return count


def longest_streak(numbers: Sequence[int], key: str) -> int:
    test = CATEGORIES[key].test
    best = run = 0
    for n in numbers:
        run = run + 1 if test(n) else 0
        best = max(best, run)
    return best


def gap(numbers: Sequence[int], key: str) -> int | None:
    """Giri passati dall'ultima uscita della categoria (ritardo); None se non è mai uscita."""
    test = CATEGORIES[key].test
    for i, n in enumerate(reversed(numbers)):
        if test(n):
            return i
    return None
