"""Secret filter rules: catch the usual credentials, leave ordinary code alone."""
import pytest

from control_center.modules.rag import secret_filter as sf


@pytest.mark.parametrize("line, rule", [
    ("'password' => 'Sup3rGeheim!',", "assignment"),
    ("$db_pass = \"k8s-prod-2024x\";", "assignment"),
    ("DB_PASSWORD='hunter2hunter2'", "assignment"),
    ("apiKey: 'a1b2c3d4e5f6g7h8',", "assignment"),
    ("  client_secret: \"abcDEF123456\"", "assignment"),
    ("'jwtSecret' => 'q9w8e7r6t5z4'", "assignment"),
    ("dsn: mysql://app:s3cr3t@db.local:3306/app", "url-credentials"),
    ("REDIS_URL=redis://default:verylongpw@cache:6379", "url-credentials"),
    ("-----BEGIN RSA PRIVATE KEY-----", "private-key"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----", "private-key"),
    ("key = AKIAIOSFODNN7EXAMPLE", "aws-access-key"),
    ("token ghp_" + "a" * 36, "github-token"),
    ("const t = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U'", "jwt"),
    ("Authorization: xoxb-1234567890-abcdefghij", "slack-token"),
])
def test_secrets_are_found(line, rule):
    hits = sf.content_hits(f"<?php\n{line}\n")
    assert [(h.line, h.rule) for h in hits][:1] == [(2, rule)]


@pytest.mark.parametrize("line", [
    "'password' => 'required|string|min:8',",          # validation rule, not a value
    "password: ['', Validators.required],",            # Angular form control
    "<input type=\"password\" name=\"password\">",
    "$password = $request->get('password');",
    "'password' => 'changeme',",
    "DB_PASSWORD='${DB_PASSWORD}'",
    "'password' => '%env(DB_PASSWORD)%',",
    "pass: 'short',",                                  # under 8 characters
    "PASSWORD: 'Passwort',",                           # i18n label
    "'password_reset' => 'Passwort zurücksetzen',",    # text with spaces
    "url: 'mysql://user:password@localhost/db'",      # documented placeholder
    "tokenType: 'Bearer'",
    "export class AuthService { token: string = ''; }",
    "const secretOfManaUrl = 'https://example.org/page'",
])
def test_ordinary_code_passes(line):
    assert sf.content_hits(line) == []


@pytest.mark.parametrize("name, hit", [
    (".env", True), (".env.local", True), ("prod.env", True), ("id_rsa", True), ("server.pem", True),
    ("secrets.yaml", True), ("Credentials.json", True), ("Services.yaml", False), ("Note.php", False),
    ("environment.ts", False),
])
def test_name_rules(name, hit):
    assert (sf.name_hit(name) is not None) is hit


def test_check_reports_line_and_rule_but_never_the_value():
    hits = sf.check("config/db.php", "<?php\nreturn [\n  'password' => 'Sup3rGeheim!',\n];\n")
    assert hits == [sf.SecretHit(3, "assignment")]
    assert "Sup3rGeheim" not in repr(hits)
    assert sf.check("config/.env.prod", "whatever") == [sf.SecretHit(None, "name:.env.*")]


def test_allow_globs_use_the_relative_path():
    assert sf.allowed("config/demo.php", ["config/demo.php"])
    assert sf.allowed("config/demo.php", ["config/*.php"])
    assert sf.allowed("config/demo.php", ["\\config\\demo.php"])     # Windows spelling in the toml
    assert not sf.allowed("config/prod.php", ["config/demo.php"])


def test_hits_are_limited_and_long_lines_stay_fast():
    text = "\n".join(f"'password' => 'secretvalue{i:04d}'," for i in range(100))
    assert len(sf.content_hits(text)) == 20
    assert sf.content_hits("x" * 1_000_000) == []
