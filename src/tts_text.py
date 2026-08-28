"""Números por extenso para a NARRAÇÃO (pt-BR).

A Alice (voz inglesa lendo português) tropeça em algarismos: em 28/08 o
"3,81%" do gancho saiu com a vírgula falada errada. Modelos de TTS leem bem o
que está escrito como se fala — então, imediatamente antes de sintetizar,
convertemos:

    3,81%          -> três vírgula oitenta e um por cento
    R$ 1.775,50    -> mil setecentos e setenta e cinco reais e cinquenta centavos
    1.200          -> mil e duzentos
    12%            -> doze por cento

Só o áudio muda (e as legendas, que nascem do alinhamento do áudio); título,
descrição e slides continuam com os algarismos. Desligável em
tts.normalize_numbers.
"""
from __future__ import annotations

import re


def _n2w(n: int) -> str:
    from num2words import num2words
    return num2words(n, lang="pt_BR").replace(",", "")


def _decimal_words(int_part: str, frac_part: str) -> str:
    frac_part = frac_part.rstrip("0") or "0"
    lead_zeros = len(frac_part) - len(frac_part.lstrip("0"))
    frac_words = ("zero " * lead_zeros) + _n2w(int(frac_part))
    return f"{_n2w(int(int_part))} vírgula {frac_words.strip()}"


def _currency(m: re.Match) -> str:
    inteiro = int(m.group(1).replace(".", ""))
    cents = m.group(2)
    words = _n2w(inteiro) + (" real" if inteiro == 1 else " reais")
    if cents and int(cents) > 0:
        c = int(cents)
        words += " e " + _n2w(c) + (" centavo" if c == 1 else " centavos")
    return words


def normalize_numbers_pt(text: str) -> str:
    """Trocar algarismos por palavras, na ordem certa (moeda > % > decimal > milhar)."""
    out = text
    # R$ 1.234,56 | R$ 985 | R$985,00
    out = re.sub(r"R\$ ?(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{1,2}))?", _currency, out)
    # 3,81% | 12% | 3.81% (ponto decimal por engano)
    out = re.sub(r"(\d+)[,.](\d+) ?%",
                 lambda m: _decimal_words(m.group(1), m.group(2)) + " por cento", out)
    out = re.sub(r"(\d+) ?%", lambda m: _n2w(int(m.group(1))) + " por cento", out)
    # decimal com vírgula fora de %: 3,81
    out = re.sub(r"(?<![\d.])(\d+),(\d+)(?![\d%])",
                 lambda m: _decimal_words(m.group(1), m.group(2)), out)
    # milhar com ponto: 1.200 / 12.345.678
    out = re.sub(r"(?<![\d,.])(\d{1,3}(?:\.\d{3})+)(?![\d,%])",
                 lambda m: _n2w(int(m.group(1).replace(".", ""))), out)
    return out
