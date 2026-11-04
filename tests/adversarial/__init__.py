"""Adversarial sandbox tests.

This package marker matters: without it ``unittest discover`` does not descend
into this directory, so ``test_sandbox_escape.py`` used to be invisible to
ordinary CI and could rot silently until a live run was attempted. It is imported
(and skipped) by every Engine CI run now.
"""
