'''
Contains backwards compatibility helpers for different python versions.
If something is relevant to HPI itself, please put it in .hpi_compat instead
'''

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if sys.version_info[:2] >= (3, 13):
    from warnings import deprecated
else:
    from typing_extensions import deprecated


# keeping just for backwards compatibility, used to have compat implementation for 3.6
if not TYPE_CHECKING:
    import sqlite3

    @deprecated('use .backup method on sqlite3.Connection directly instead')
    def sqlite_backup(*, source: sqlite3.Connection, dest: sqlite3.Connection, **kwargs) -> None:
        # TODO warn here?
        source.backup(dest, **kwargs)

    # keeping for runtime backwards compatibility (added in 3.9)
    @deprecated('use .removeprefix method on string directly instead')
    def removeprefix(text: str, prefix: str) -> str:
        return text.removeprefix(prefix)

    @deprecated('use .removesuffix method on string directly instead')
    def removesuffix(text: str, suffix: str) -> str:
        return text.removesuffix(suffix)

    ##

    ## used to have compat function before 3.8 for these, keeping for runtime back compatibility
    from functools import cached_property
    from typing import Literal, Protocol, TypedDict
##


from typing import ParamSpec


# bisect_left doesn't have a 'key' parameter (which we use)
# till python3.10
from bisect import bisect_left


from datetime import datetime

if sys.version_info[:2] >= (3, 11):
    fromisoformat = datetime.fromisoformat
else:
    # fromisoformat didn't support Z as "utc" before 3.11
    # https://docs.python.org/3/library/datetime.html#datetime.datetime.fromisoformat

    def fromisoformat(date_string: str) -> datetime:
        if date_string.endswith('Z'):
            date_string = date_string[:-1] + '+00:00'
        return datetime.fromisoformat(date_string)


def test_fromisoformat() -> None:
    from datetime import timezone

    # fmt: off
    # feedbin has this format
    assert fromisoformat('2020-05-01T10:32:02.925961Z') == datetime(
        2020, 5, 1, 10, 32, 2, 925961, timezone.utc,
    )

    # polar has this format
    assert fromisoformat('2018-11-28T22:04:01.304Z') == datetime(
        2018, 11, 28, 22, 4, 1, 304000, timezone.utc,
    )

    # stackexchange, runnerup has this format
    assert fromisoformat('2020-11-30T00:53:12Z') == datetime(
        2020, 11, 30, 0, 53, 12, 0, timezone.utc,
    )
    # fmt: on

    # arbtt has this format (sometimes less/more than 6 digits in milliseconds)
    # TODO doesn't work atm, not sure if really should be supported...
    # maybe should have flags for weird formats?
    # assert isoparse('2017-07-18T18:59:38.21731Z') == datetime(
    #     2017, 7, 18, 18, 59, 38, 217310, timezone.utc,
    # )


from types import NoneType
from typing import TypeAlias


if sys.version_info[:2] >= (3, 11):
    from typing import Never, assert_never, assert_type
else:
    from typing_extensions import Never, assert_never, assert_type
