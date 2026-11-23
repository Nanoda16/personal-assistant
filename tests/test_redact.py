from personal_assistant.redact import redact_text


def test_redact_sensitive_patterns() -> None:
    raw = (
        "key=sk-abcdefghijklmnop123456 "
        "gh=ghp_1234567890abcdefghij1234 "
        "aws=AKIAIOSFODNN7EXAMPLE "
        "auth=Bearer eyJhbGciOiJIUzI1NiJ9.payload "
        "password=my_secret_pass "
        "密码：ab123456 "
        "id=11010119900307123X "
        "phone=13812345678"
    )
    cleaned = redact_text(raw)
    assert "sk-abcdefghijklmnop123456" not in cleaned
    assert "ghp_1234567890abcdefghij1234" not in cleaned
    assert "AKIAIOSFODNN7EXAMPLE" not in cleaned
    assert "eyJhbGciOiJIUzI1NiJ9" not in cleaned
    assert "my_secret_pass" not in cleaned
    assert "ab123456" not in cleaned
    assert "11010119900307123X" not in cleaned
    assert "13812345678" not in cleaned
    assert "[REDACTED:api_key]" in cleaned
    assert "[REDACTED:github_token]" in cleaned
    assert "[REDACTED:aws_key]" in cleaned
    assert "[REDACTED:id_card]" in cleaned
    assert "[REDACTED:phone]" in cleaned
