from app import aliases


class TestSlugify:
    def test_simple_lowercase(self):
        assert aliases.slugify("netflix") == "netflix"

    def test_capitalization(self):
        assert aliases.slugify("Netflix") == "netflix"

    def test_spaces_become_underscores(self):
        assert aliases.slugify("Bank of America") == "bank_of_america"

    def test_punctuation_becomes_underscore(self):
        assert aliases.slugify("Bank of America!") == "bank_of_america"

    def test_hyphen_becomes_underscore(self):
        assert aliases.slugify("anti-spam list") == "anti_spam_list"

    def test_dots_removed(self):
        assert aliases.slugify("v1.0") == "v1_0"

    def test_collapses_runs(self):
        assert aliases.slugify("a   b") == "a_b"

    def test_trims_underscores(self):
        assert aliases.slugify("___hi___") == "hi"

    def test_empty(self):
        assert aliases.slugify("") == ""

    def test_only_punctuation(self):
        assert aliases.slugify("!!!") == ""

    def test_unicode_dropped(self):
        # Non-ASCII letters are not in [a-z0-9], so they become underscores
        assert aliases.slugify("café") == "caf"


class TestClassify:
    TAG = "g-"

    def test_managed_labeled(self):
        kind, label = aliases.classify("g-a3f82c11-netflix", self.TAG)
        assert kind == "managed_labeled"
        assert label == "netflix"

    def test_managed_unlabeled(self):
        kind, label = aliases.classify("g-a3f82c11", self.TAG)
        assert kind == "managed_unlabeled"
        assert label == ""

    def test_managed_label_with_underscores(self):
        kind, label = aliases.classify("g-a3f82c11-bank_of_america", self.TAG)
        assert kind == "managed_labeled"
        assert label == "bank_of_america"

    def test_no_tag_unmanaged(self):
        kind, _ = aliases.classify("gitea", self.TAG)
        assert kind == "unmanaged"

    def test_tag_but_no_valid_random(self):
        # Starts with tag but the remainder isn't 8 hex chars
        kind, _ = aliases.classify("g-zzzzzzzz", self.TAG)
        assert kind == "unmanaged"

    def test_tag_random_but_label_has_hyphen(self):
        # Labels can't contain hyphens (those are the separator), so a label
        # containing '-' should mean someone created this outside Guise's rules
        kind, _ = aliases.classify("g-a3f82c11-bad-label", self.TAG)
        assert kind == "unmanaged"

    def test_tag_random_empty_label_after_dash(self):
        kind, _ = aliases.classify("g-a3f82c11-", self.TAG)
        assert kind == "unmanaged"

    def test_legacy_alias_with_dot(self):
        kind, _ = aliases.classify("first.last", self.TAG)
        assert kind == "unmanaged"

    def test_random_only_without_tag(self):
        # 8 hex chars alone are NOT Guise-managed; tag is required
        kind, _ = aliases.classify("a3f82c11", self.TAG)
        assert kind == "unmanaged"

    def test_custom_tag(self):
        kind, label = aliases.classify("guise-a3f82c11-netflix", "guise-")
        assert kind == "managed_labeled"
        assert label == "netflix"


class TestMakeLocalPart:
    def test_with_label(self):
        result = aliases.make_local_part("g-", "netflix", random="a3f82c11")
        assert result == "g-a3f82c11-netflix"

    def test_without_label(self):
        result = aliases.make_local_part("g-", "", random="a3f82c11")
        assert result == "g-a3f82c11"

    def test_random_auto_generated(self):
        result = aliases.make_local_part("g-", "netflix")
        assert result.startswith("g-")
        assert result.endswith("-netflix")
        # 'g-' + 8 hex + '-' + 'netflix'
        assert len(result) == len("g-") + 8 + len("-netflix")


class TestParseAliasList:
    def test_typical_output(self):
        stdout = (
            "* postmaster@example.com admin@example.com\n"
            "\n"
            "* gitea@example.com admin@example.com\n"
            "\n"
            "* g-a3f82c11-netflix@example.com alice@example.com\n"
        )
        rows = aliases.parse_alias_list(stdout)
        assert rows == [
            ("postmaster@example.com", "admin@example.com"),
            ("gitea@example.com", "admin@example.com"),
            ("g-a3f82c11-netflix@example.com", "alice@example.com"),
        ]

    def test_skips_blank_and_garbage(self):
        stdout = "no asterisk\n  \n* one@example.com two@example.com\n* incomplete\n"
        rows = aliases.parse_alias_list(stdout)
        assert rows == [("one@example.com", "two@example.com")]

    def test_extra_whitespace(self):
        rows = aliases.parse_alias_list("*    a@x.com    b@x.com   \n")
        assert rows == [("a@x.com", "b@x.com")]


class TestBuildView:
    TAG = "g-"

    def test_filters_by_target(self):
        rows = [
            ("g-a3f82c11-netflix@example.com", "alice@example.com"),
            ("g-deadbeef-spotify@example.com", "bob@example.com"),
            ("postmaster@example.com", "admin@example.com"),
        ]
        view = aliases.build_view(rows, "alice@example.com", self.TAG)
        assert len(view["managed"]) == 1
        assert view["managed"][0].label == "netflix"
        assert view["unmanaged"] == []

    def test_includes_unmanaged_pointing_to_user(self):
        rows = [
            ("g-a3f82c11-netflix@example.com", "alice@example.com"),
            ("oldalias@example.com", "alice@example.com"),
        ]
        view = aliases.build_view(rows, "alice@example.com", self.TAG)
        assert len(view["managed"]) == 1
        assert len(view["unmanaged"]) == 1
        assert view["unmanaged"][0].kind == "unmanaged"

    def test_managed_unlabeled_in_managed_bucket(self):
        rows = [("g-a3f82c11@example.com", "alice@example.com")]
        view = aliases.build_view(rows, "alice@example.com", self.TAG)
        assert len(view["managed"]) == 1
        assert view["managed"][0].kind == "managed_unlabeled"
        assert view["managed"][0].label == ""


class TestAnsiStrip:
    def test_strips_color_codes(self):
        text = "\x1b[0;31m* a@x b@x\x1b[0m"
        assert aliases._strip_ansi(text) == "* a@x b@x"
