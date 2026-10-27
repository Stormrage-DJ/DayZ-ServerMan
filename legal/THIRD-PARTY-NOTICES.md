# Third-Party Notices

This index lists third-party components with their licence evidence. It is
policy support, not legal advice.

## Development tools

These tools run only in development and in the continuous integration. They are
not part of `runnable/`, and the application does not distribute them.

### MIT

- [pyflakes](https://github.com/PyCQA/pyflakes) (`3.4.0`, wheel SHA-256
  `f742a7dbd0d9cb9ea41e9a24a918996e8170c799fa528688d40dd582c8265f4f`) -
  [License](./third-party/python/pyflakes-3.4.0/LICENSE)
  - Use: development-only; `tests/test_undefined_names.py` reports undefined
    names. Pinned in `requirements-dev.txt`.
  - Source: https://pypi.org/project/pyflakes/3.4.0/, acquired 2026-10-10.
  - Dependencies: none.
  - Changes: none.
  - Additional evidence: none. The licence file is the unchanged
    `pyflakes-3.4.0.dist-info/LICENSE` from the wheel.
