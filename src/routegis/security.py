from __future__ import annotations

import re
from urllib.parse import quote, quote_plus, unquote


_URL_WITH_QUERY_PATTERN = re.compile(r"https?://[^\s<>'\"]+\?[^\s<>'\"]+", re.IGNORECASE)
_MAX_ENCODING_DEPTH = 16

_EncodedForm = tuple[str, tuple[bool, ...]]


def _encode_form(form: _EncodedForm, *, plus: bool) -> _EncodedForm:
    text, flexible_hex = form
    encoder = quote_plus if plus else quote
    encoded_text: list[str] = []
    encoded_flexible_hex: list[bool] = []
    for character, flexible in zip(text, flexible_hex):
        encoded = encoder(character, safe="")
        if encoded == character:
            encoded_text.append(character)
            encoded_flexible_hex.append(flexible)
            continue
        index = 0
        while index < len(encoded):
            if encoded[index] == "%" and index + 2 < len(encoded):
                encoded_text.append("%")
                encoded_flexible_hex.append(False)
                for hex_character in encoded[index + 1 : index + 3]:
                    encoded_text.append(hex_character)
                    encoded_flexible_hex.append(hex_character.upper() in "ABCDEF")
                index += 3
                continue
            encoded_text.append(encoded[index])
            encoded_flexible_hex.append(False)
            index += 1
    return "".join(encoded_text), tuple(encoded_flexible_hex)


def _encoded_forms(secret: str, maximum_length: int) -> list[_EncodedForm]:
    initial = (secret, (False,) * len(secret))
    forms = [initial]
    seen = {initial}
    frontier = [initial]
    for _ in range(_MAX_ENCODING_DEPTH):
        next_frontier: list[_EncodedForm] = []
        for form in frontier:
            for plus in (False, True):
                encoded = _encode_form(form, plus=plus)
                if encoded in seen or len(encoded[0]) > maximum_length:
                    continue
                seen.add(encoded)
                forms.append(encoded)
                next_frontier.append(encoded)
        if not next_frontier:
            break
        frontier = next_frontier
    return forms


def _form_pattern(form: _EncodedForm) -> re.Pattern[str]:
    text, flexible_hex = form
    parts: list[str] = []
    for character, flexible in zip(text, flexible_hex):
        if flexible and character.upper() in "ABCDEF":
            parts.append(f"[{character.upper()}{character.lower()}]")
        else:
            parts.append(re.escape(character))
    return re.compile("".join(parts))


def redact_sensitive_text(
    message: object | None,
    *secrets: str,
    redact_urls: bool = True,
) -> str | None:
    if message is None:
        return None
    redacted = str(message)
    for secret in dict.fromkeys(value for secret in secrets for value in (secret, unquote(secret))):
        if not secret:
            continue
        forms = sorted(
            _encoded_forms(secret, len(redacted)),
            key=lambda form: len(form[0]),
            reverse=True,
        )
        for form in forms:
            redacted = _form_pattern(form).sub("***", redacted)
    if redact_urls:
        return _URL_WITH_QUERY_PATTERN.sub("[redacted URL]", redacted)
    return redacted
