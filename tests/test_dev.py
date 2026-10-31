"""The developer command. Offline: no verb is actually run, since several spend
money; this only checks the dispatcher and that every verb is importable."""

import importlib

from col4a1a2.dev import VERBS, main


def test_help_lists_every_verb(capsys):
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    for verb in VERBS:
        assert verb in out


def test_no_arguments_prints_usage_and_fails(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().out


def test_unknown_verb_is_rejected(capsys):
    assert main(["frobnicate"]) == 2
    assert "unknown verb" in capsys.readouterr().err


def test_every_verb_exposes_main_taking_argv():
    for verb in VERBS:
        mod = importlib.import_module(f"col4a1a2.dev.{verb}")
        assert callable(mod.main), verb
        assert mod.__doc__, f"{verb} has no docstring, so --help would show nothing"


def test_probe_with_bad_arguments_prints_usage_without_calling_the_api(capsys):
    assert main(["probe"]) == 2
    assert "probe" in capsys.readouterr().out
