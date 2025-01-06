"""
This lets you query, order, sort and filter items from one or more sources

The main entrypoint to this library is the 'select' function below; try:
python3 -c "from my.core.query import select; help(select)"
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import itertools
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import (
    Any,
    NamedTuple,
    Optional,
    TypeVar,
)
from collections.abc import Callable

import more_itertools

from . import error as err
from .error import Res, unwrap
from .types import is_namedtuple
from .warnings import low

T = TypeVar("T")
ET = Res[T]


U = TypeVar("U")
# In a perfect world, the return value from a OrderFunc would just be U,
# not Optional[U]. However, since this has to deal with so many edge
# cases, there's a possibility that the functions generated by
# _generate_order_by_func can't find an attribute
OrderFunc = Callable[[ET], Optional[U]]
Where = Callable[[ET], bool]


# the generated OrderFunc couldn't handle sorting this
class Unsortable(NamedTuple):
    obj: Any


class QueryException(ValueError):
    """Used to differentiate query-related errors, so the CLI interface is more expressive"""

    pass


def locate_function(module_name: str, function_name: str) -> Callable[[], Iterable[ET]]:
    """
    Given a module name and a function, returns the corresponding function.
    Since we're in the query module, it is assumed that this returns an
    iterable of objects of some kind, which we want to query over, though
    that isn't required
    """
    try:
        mod = importlib.import_module(module_name)
        for fname, f in inspect.getmembers(mod, inspect.isfunction):
            if fname == function_name:
                return f
        # in case the function is defined dynamically,
        # like with a globals().setdefault(...) or a module-level __getattr__ function
        func = getattr(mod, function_name, None)
        if func is not None and callable(func):
            return func
    except Exception as e:
        raise QueryException(str(e))  # noqa: B904
    raise QueryException(f"Could not find function '{function_name}' in '{module_name}'")


def locate_qualified_function(qualified_name: str) -> Callable[[], Iterable[ET]]:
    """
    As an example, 'my.reddit.rexport.comments' -> locate_function('my.reddit.rexport', 'comments')
    """
    if "." not in qualified_name:
        raise QueryException("Could not find a '.' in the function name, e.g. my.reddit.rexport.comments")
    rdot_index = qualified_name.rindex(".")
    return locate_function(qualified_name[:rdot_index], qualified_name[rdot_index + 1 :])


def attribute_func(obj: T, where: Where, default: U | None = None) -> OrderFunc | None:
    """
    Attempts to find an attribute which matches the 'where_function' on the object,
    using some getattr/dict checks. Returns a function which when called with
    this object returns the value which the 'where' matched against

    As an example:

    from typing import NamedTuple
    from datetime import datetime
    from my.core.query import attribute_func

    class A(NamedTuple):
        x: int
        y: datetime

    val = A(x=4, y=datetime.now())
    val.y
    > datetime.datetime(2021, 4, 5, 10, 52, 14, 395195)
    orderfunc = attribute_func(val, where=lambda o: isinstance(o, datetime))
    orderfunc(val)
    > datetime.datetime(2021, 4, 5, 10, 52, 14, 395195)
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if where(v):
                return lambda o: o.get(k, default)  # type: ignore[union-attr]
    elif dataclasses.is_dataclass(obj):
        for field_name in obj.__annotations__.keys():
            if where(getattr(obj, field_name)):
                return lambda o: getattr(o, field_name, default)
    elif is_namedtuple(obj):
        assert hasattr(obj, '_fields'), "Could not find '_fields' on attribute which is assumed to be a NamedTuple"
        for field_name in getattr(obj, '_fields'):
            if where(getattr(obj, field_name)):
                return lambda o: getattr(o, field_name, default)
    # try using inspect.getmembers (like 'dir()') even if the dataclass/NT checks failed,
    # since the attribute one is searching for might be a @property
    for k, v in inspect.getmembers(obj):
        if where(v):
            return lambda o: getattr(o, k, default)
    return None


def _generate_order_by_func(
    obj_res: Res[T],
    *,
    key: str | None = None,
    where_function: Where | None = None,
    default: U | None = None,
    force_unsortable: bool = False,
) -> OrderFunc | None:
    """
    Accepts an object Res[T] (Instance of some class or Exception)

    If its an error, the generated function returns None

    Most of the time, you'd want to provide at least a 'key', a 'where_function' or a 'default'.
    You can provide both a 'where_function' and a default, or a 'key' and a default,
    in case the 'where_function' doesn't work for a particular type/you hit an error

    If a 'default' is provided, it is used for Exceptions and if an
    OrderFunc function could not be determined for this type

    If 'force_unsortable' is True, that means this returns an OrderFunc
    which returns None for any input -- which would wrap items of this
    type in an Unsortable object

    If a key is given (the user specified which attribute), the function
    returns that key from the object
    tries to find that key on the object

    Attempts to find an attribute which matches the 'where_function' on the object,
    using some getattr/dict checks. Returns a function which when called with
    this object returns the value to order by
    """
    if isinstance(obj_res, Exception):
        if default is not None:
            return lambda _o: default
        else:
            # perhaps this should be removed? as errors are now silently wrapped into Unsortable
            # then again, its not strange that a src returning an error should warn, just don't cause a fatal error
            low(f"""While creating order_by function, encountered exception '{type(obj_res)}: {obj_res}'
Value to order_by unknown, provide a 'default', filter exceptions with a 'where' predicate or
pass 'drop_exceptions' to ignore exceptions""")
            return lambda _o: None

    # shouldn't raise an error, as we return above if its an exception
    obj: T = unwrap(obj_res)

    if key is not None:

        # in these cases, if your key existed on the initial Res[E] (instance that was passed to
        # _generate_order_by_func and generates the OrderFunc)
        # to run, but doesn't on others, it will return None in those cases
        # If the interface to your ADT is not standard or very sparse, its better
        # that you manually write an OrderFunc which
        # handles the edge cases, or provide a default
        # See tests for an example
        if isinstance(obj, dict):
            if key in obj:  # acts as predicate instead of where_function
                return lambda o: o.get(key, default)  # type: ignore[union-attr]
        else:
            if hasattr(obj, key):
                return lambda o: getattr(o, key, default)

    # Note: if the attribute you're ordering by is an Optional type,
    # and on some objects it'll return None, the getattr(o, field_name, default) won't
    # use the default, since it finds the attribute (it just happens to be set to None)
    # perhaps this should do something like: 'lambda o: getattr(o, k, default) or default'
    # that would fix the case, but is additional work. Perhaps the user should instead
    # write a 'where' function, to check for that 'isinstance' on an Optional field,
    # and not include those objects in the src iterable... becomes a bit messy with multiple sources

    # user must provide either a key or a where predicate
    if where_function is not None:
        func: OrderFunc | None = attribute_func(obj, where_function, default)
        if func is not None:
            return func

    if default is not None:
        # warn here? it seems like you typically wouldn't want to just set the order by to
        # the same value everywhere, but maybe you did this on purpose?
        return lambda _o: default
    elif force_unsortable:
        # generate a dummy function which returns None
        # this causes this type of object to be classified as an unsortable item
        return lambda _o: None
    else:
        return None  # couldn't compute a OrderFunc for this class/instance


# currently using the 'key set' as a proxy for 'this is the same type of thing'
def _determine_order_by_value_key(obj_res: ET) -> Any:
    """
    Returns either the class, or a tuple of the dictionary keys
    """
    key = obj_res.__class__
    if key is dict:
        # assuming same keys signify same way to determine ordering
        return tuple(obj_res.keys())  # type: ignore[union-attr]
    return key


def _drop_unsorted(itr: Iterator[ET], orderfunc: OrderFunc) -> Iterator[ET]:
    for o in itr:
        if isinstance(o, Unsortable):
            continue
        ordval = orderfunc(o)
        if ordval is None:
            continue
        yield o


# try getting the first value from the iterator
# similar to my.core.common.warn_if_empty? this doesn't go through the whole iterator though
def _peek_iter(itr: Iterator[ET]) -> tuple[ET | None, Iterator[ET]]:
    itr = more_itertools.peekable(itr)
    try:
        first_item = itr.peek()
    except StopIteration:
        return None, itr
    else:
        return first_item, itr


# similar to 'my.core.error.sort_res_by'?
def _wrap_unsorted(itr: Iterator[ET], orderfunc: OrderFunc) -> tuple[Iterator[Unsortable], Iterator[ET]]:
    unsortable: list[Unsortable] = []
    sortable: list[ET] = []
    for o in itr:
        # if input to select was another select
        if isinstance(o, Unsortable):
            unsortable.append(o)
            continue
        ordval = orderfunc(o)
        if ordval is None:
            unsortable.append(Unsortable(o))
        else:
            sortable.append(o)
    return iter(unsortable), iter(sortable)


# return two iterators, the first being the wrapped unsortable items,
# the second being items for which orderfunc returned a non-none value
def _handle_unsorted(
    itr: Iterator[ET],
    *,
    orderfunc: OrderFunc,
    drop_unsorted: bool,
    wrap_unsorted: bool
) -> tuple[Iterator[Unsortable], Iterator[ET]]:
    # prefer drop_unsorted to wrap_unsorted, if both were present
    if drop_unsorted:
        return iter([]), _drop_unsorted(itr, orderfunc)
    elif wrap_unsorted:
        return _wrap_unsorted(itr, orderfunc)
    else:
        # neither flag was present
        return iter([]), itr


# handles creating an order_value function, using a lookup for
# different types. ***This consumes the iterator***, so
# you should definitely itertoolts.tee it beforehand
# as to not exhaust the values
def _generate_order_value_func(itr: Iterator[ET], order_value: Where, default: U | None = None) -> OrderFunc:
    # TODO: add a kwarg to force lookup for every item? would sort of be like core.common.guess_datetime then
    order_by_lookup: dict[Any, OrderFunc] = {}

    # need to go through a copy of the whole iterator here to
    # pre-generate functions to support sorting mixed types
    for obj_res in itr:
        key: Any = _determine_order_by_value_key(obj_res)
        if key not in order_by_lookup:
            keyfunc: OrderFunc | None = _generate_order_by_func(
                obj_res,
                where_function=order_value,
                default=default,
                force_unsortable=True)
            # should never be none, as we have force_unsortable=True
            assert keyfunc is not None
            order_by_lookup[key] = keyfunc

    # todo: cache results from above _determine_order_by_value_key call and use here somehow?
    # would require additional state
    # order_by_lookup[_determine_order_by_value_key(o)] returns a function which
    # accepts o, and returns the value which sorted can use to order this by
    return lambda o: order_by_lookup[_determine_order_by_value_key(o)](o)


# handles the arguments from the user, creating a order_value function
# at least one of order_by, order_key or order_value must have a value
def _handle_generate_order_by(
    itr,
    *,
    order_by: OrderFunc | None = None,
    order_key: str | None = None,
    order_value: Where | None = None,
    default: U | None = None,
) -> tuple[OrderFunc | None, Iterator[ET]]:
    order_by_chosen: OrderFunc | None = order_by  # if the user just supplied a function themselves
    if order_by is not None:
        return order_by, itr
    if order_key is not None:
        first_item, itr = _peek_iter(itr)
        if first_item is None:
            # signify the iterator was empty, return immediately from parent
            return None, itr
        # try to use a key, if it was supplied
        # order_key doesn't use local state - it just tries to find the passed
        # attribute, or default to the 'default' value. As mentioned above,
        # best used for items with a similar structure
        # note: this could fail if the first item doesn't have a matching attr/key?
        order_by_chosen = _generate_order_by_func(first_item, key=order_key, default=default)
        if order_by_chosen is None:
            raise QueryException(f"Error while ordering: could not find {order_key} on {first_item}")
        return order_by_chosen, itr
    if order_value is not None:
        itr, itr2 = itertools.tee(itr, 2)
        order_by_chosen = _generate_order_value_func(itr2, order_value, default)
        return order_by_chosen, itr
    raise QueryException("Could not determine a way to order src iterable - at least one of the order args must be set")


def select(
    src: Iterable[ET] | Callable[[], Iterable[ET]],
    *,
    where: Where | None = None,
    order_by: OrderFunc | None = None,
    order_key: str | None = None,
    order_value: Where | None = None,
    default: U | None = None,
    reverse: bool = False,
    limit: int | None = None,
    drop_unsorted: bool = False,
    wrap_unsorted: bool = True,
    warn_exceptions: bool = False,
    warn_func: Callable[[Exception], None] | None = None,
    drop_exceptions: bool = False,
    raise_exceptions: bool = False,
) -> Iterator[ET]:
    """
    A function to query, order, sort and filter items from one or more sources
    This supports iterables and lists of mixed types (including handling errors),
    by allowing you to provide custom predicates (functions) which can sort
    by a function, an attribute, dict key, or by the attributes values.

    Since this supports mixed types, there's always a possibility
    of KeyErrors or AttributeErrors while trying to find some value to order by,
    so this provides multiple mechanisms to deal with that

    'where' lets you filter items before ordering, to remove possible errors
    or filter the iterator by some condition

    There are multiple ways to instruct select on how to order items. The most
    flexible is to provide an 'order_by' function, which takes an item in the
    iterator, does any custom checks you may want and then returns the value to sort by

    'order_key' is best used on items which have a similar structure, or have
    the same attribute name for every item in the iterator. If you have a
    iterator of objects whose datetime is accessed by the 'timestamp' attribute,
    supplying order_key='timestamp' would sort by that (dictionary or attribute) key

    'order_value' is the most confusing, but often the most useful. Instead of
    testing against the keys of an item, this allows you to write a predicate
    (function) to test against its values (dictionary, NamedTuple, dataclass, object).
    If you had an iterator of mixed types and wanted to sort by the datetime,
    but the attribute to access the datetime is different on each type, you can
    provide `order_value=lambda v: isinstance(v, datetime)`, and this will
    try to find that value for each type in the iterator, to sort it by
    the value which is received when the predicate is true

    'order_value' is often used in the 'hpi query' interface, because of its brevity.
    Just given the input function, this can typically sort it by timestamp with
    no human intervention. It can sort of be thought as an educated guess,
    but it can always be improved by providing a more complete guess function

    Note that 'order_value' is also the most computationally expensive, as it has
    to copy the iterator in memory (using itertools.tee) to determine how to order it
    in memory

    The 'drop_exceptions', 'raise_exceptions', 'warn_exceptions' let you ignore or raise
    when the src contains exceptions. The 'warn_func' lets you provide a custom function
    to call when an exception is encountered instead of using the 'warnings' module

    src:            an iterable of mixed types, or a function to be called,
                    as the input to this function

    where:          a predicate which filters the results before sorting

    order_by:       a function which when given an item in the src,
                    returns the value to sort by. Similar to the 'key' value
                    typically passed directly to 'sorted'

    order_key:      a string which represents a dict key or attribute name
                    to use as they key to sort by

    order_value:    predicate which determines which attribute on an ADT-like item to sort by,
                    when given its value. lambda o: isinstance(o, datetime) is commonly passed to sort
                    by datetime, without knowing the attributes or interface for the items in the src

    default:        while ordering, if the order for an object cannot be determined,
                    use this as the default value

    reverse:        reverse the order of the resulting iterable

    limit:          limit the results to this many items

    drop_unsorted:  before ordering, drop any items from the iterable for which a
                    order could not be determined. False by default

    wrap_unsorted:  before ordering, wrap any items into an 'Unsortable' object. Place
                    them at the front of the list. True by default

    drop_exceptions: ignore any exceptions from the src

    raise_exceptions: raise exceptions when received from the input src
    """

    it: Iterable[ET] = []  # default
    if callable(src):
        # hopefully this returns an iterable and not something that causes a bunch of lag when its called?
        # should typically not be the common case, but giving the option to
        # provide a function as input anyways
        it = src()
    else:
        # assume it is already an iterable
        if not isinstance(src, Iterable):
            low(f"""Input was neither a function, or some iterable
Expected 'src' to be an Iterable, but found {type(src).__name__}...
Will attempt to call iter() on the value""")
        it = src

    # try/catch an explicit iter() call to making this an Iterator,
    # to validate the input as something other helpers here can work with,
    # else raise a QueryException
    try:
        itr: Iterator[ET] = iter(it)
    except TypeError as t:
        raise QueryException("Could not convert input src to an Iterator: " + str(t))  # noqa: B904

    # if both drop_exceptions and drop_exceptions are provided for some reason,
    # should raise exceptions before dropping them
    if raise_exceptions:
        itr = err.raise_exceptions(itr)

    if drop_exceptions:
        itr = err.drop_exceptions(itr)

    if warn_exceptions:
        itr = err.warn_exceptions(itr, warn_func=warn_func)

    if where is not None:
        itr = filter(where, itr)

    if order_by is not None or order_key is not None or order_value is not None:
        order_by_chosen, itr = _handle_generate_order_by(itr, order_by=order_by,
                                                         order_key=order_key,
                                                         order_value=order_value,
                                                         default=default)

        # signifies itr was filtered down to no data
        if order_by_chosen is None:
            # previously would send an warning message here,
            # but sending the warning discourages this use-case
            # e.g. take this iterable and see if I've had an event in
            # the last week, else notify me to do something
            #
            # low("""While determining order_key, encountered empty iterable.
            # Your 'src' may have been empty of the 'where' clause filtered the iterable to nothing""")
            return itr

        assert order_by_chosen is not None
        # note: can't just attach sort unsortable values in the same iterable as the
        # other items because they don't have any lookups for order_key or functions
        # to handle items in the order_by_lookup dictionary
        unsortable, itr = _handle_unsorted(
            itr,
            orderfunc=order_by_chosen,
            drop_unsorted=drop_unsorted,
            wrap_unsorted=wrap_unsorted,
        )

        # run the sort, with the computed order by function
        itr = iter(sorted(itr, key=order_by_chosen, reverse=reverse))  # type: ignore[arg-type]

        # re-attach unsortable values to the front/back of the list
        if reverse:
            itr = itertools.chain(itr, unsortable)
        else:
            itr = itertools.chain(unsortable, itr)
    else:
        # if not already done in the order_by block, reverse if specified
        if reverse:
            itr = more_itertools.always_reversible(itr)

    # apply limit argument
    if limit is not None:
        return itertools.islice(itr, limit)

    return itr


# classes to use in tests, need to be defined at the top level
# because of a mypy bug
class _Int(NamedTuple):
    x: int


# to test order_key with compatible orderable (int, float) types
class _Float(NamedTuple):
    x: float


def test_basic_orders() -> None:

    import random

    def basic_iter() -> Iterator[_Int]:
        for v in range(1, 6):
            yield _Int(v)

    def filter_two(obj: Any) -> bool:
        return obj.x != 2

    res = list(select(basic_iter(), where=filter_two, reverse=True))
    assert res == [_Int(5), _Int(4), _Int(3), _Int(1)]

    input_items = list(basic_iter())
    random.shuffle(input_items)

    res = list(select(input_items, order_key="x"))
    assert res == [_Int(1), _Int(2), _Int(3), _Int(4), _Int(5)]

    # default int ordering
    def custom_order_by(obj: Any) -> Any:
        return getattr(obj, "x")

    # sort random ordered list, only return first two items
    res = list(select(input_items, where=filter_two, order_by=custom_order_by, limit=2))
    assert res == [_Int(1), _Int(3)]

    # filter produces empty iterator (previously this used to warn, doesn't anymore)
    res = list(select(input_items, where=lambda o: o is None, order_key="x"))
    assert len(res) == 0


def test_order_key_multi_type() -> None:

    def basic_iter() -> Iterator[_Int]:
        for v in range(1, 6):
            yield _Int(v)

    def floaty_iter() -> Iterator[_Float]:
        for v in range(1, 6):
            yield _Float(float(v + 0.5))

    res = list(select(itertools.chain(basic_iter(), floaty_iter()), order_key="x"))
    assert res == [
        _Int(1), _Float(1.5),
        _Int(2), _Float(2.5),
        _Int(3), _Float(3.5),
        _Int(4), _Float(4.5),
        _Int(5), _Float(5.5),
    ]


def test_couldnt_determine_order() -> None:

    res = list(select(iter([object()]), order_value=lambda o: isinstance(o, datetime)))
    assert len(res) == 1
    assert isinstance(res[0], Unsortable)
    assert type(res[0].obj) is object


# same value type, different keys, with clashing keys
class _A(NamedTuple):
    x: datetime
    y: int
    z: int


class _B(NamedTuple):
    y: datetime


# move these to tests/? They are re-used so much in the tests below,
# not sure where the best place for these is
def _mixed_iter() -> Iterator[_A | _B]:
    yield _A(x=datetime(year=2009, month=5, day=10, hour=4, minute=10, second=1), y=5, z=10)
    yield _B(y=datetime(year=2015, month=5, day=10, hour=4, minute=10, second=1))
    yield _A(x=datetime(year=2005, month=5, day=10, hour=4, minute=10, second=1), y=10, z=2)
    yield _A(x=datetime(year=2009, month=3, day=10, hour=4, minute=10, second=1), y=12, z=1)
    yield _B(y=datetime(year=1995, month=5, day=10, hour=4, minute=10, second=1))
    yield _A(x=datetime(year=2005, month=4, day=10, hour=4, minute=10, second=1), y=2, z=-5)


def _mixed_iter_errors() -> Iterator[Res[_A | _B]]:
    m = _mixed_iter()
    yield from itertools.islice(m, 0, 3)
    yield RuntimeError("Unhandled error!")
    yield from m


def test_order_value() -> None:

    # if the value for some attribute on this item is a datetime
    sorted_by_datetime = list(select(_mixed_iter(), order_value=lambda o: isinstance(o, datetime)))
    assert sorted_by_datetime == [
        _B(y=datetime(year=1995, month=5, day=10, hour=4, minute=10, second=1)),
        _A(x=datetime(year=2005, month=4, day=10, hour=4, minute=10, second=1), y=2, z=-5),
        _A(x=datetime(year=2005, month=5, day=10, hour=4, minute=10, second=1), y=10, z=2),
        _A(x=datetime(year=2009, month=3, day=10, hour=4, minute=10, second=1), y=12, z=1),
        _A(x=datetime(year=2009, month=5, day=10, hour=4, minute=10, second=1), y=5, z=10),
        _B(y=datetime(year=2015, month=5, day=10, hour=4, minute=10, second=1)),
    ]


def test_key_clash() -> None:

    import pytest

    # clashing keys causes errors if you use order_key
    with pytest.raises(TypeError, match=r"not supported between instances of 'datetime.datetime' and 'int'"):
        list(select(_mixed_iter(), order_key="y"))


def test_wrap_unsortable() -> None:

    from collections import Counter

    # by default, wrap unsortable
    res = list(select(_mixed_iter(), order_key="z"))
    assert Counter(type(t).__name__ for t in res) == Counter({"_A": 4, "Unsortable": 2})


def test_disabled_wrap_unsorted() -> None:

    import pytest

    # if disabled manually, should raise error
    with pytest.raises(TypeError, match=r"not supported between instances of 'NoneType' and 'int'"):
        list(select(_mixed_iter(), order_key="z", wrap_unsorted=False))


def test_drop_unsorted() -> None:

    from collections import Counter

    # test drop unsortable, should remove them before the 'sorted' call
    res = list(select(_mixed_iter(), order_key="z", wrap_unsorted=False, drop_unsorted=True))
    assert len(res) == 4
    assert Counter(type(t).__name__ for t in res) == Counter({"_A": 4})


def test_drop_exceptions() -> None:

    assert more_itertools.ilen(_mixed_iter_errors()) == 7

    # drop exceptions
    res = list(select(_mixed_iter_errors(), order_value=lambda o: isinstance(o, datetime), drop_exceptions=True))
    assert len(res) == 6


def test_raise_exceptions() -> None:

    import pytest

    # raise exceptions
    with pytest.raises(RuntimeError) as r:
        select(_mixed_iter_errors(), order_value=lambda o: isinstance(o, datetime), raise_exceptions=True)
    assert str(r.value) == "Unhandled error!"


def test_wrap_unsortable_with_error_and_warning() -> None:

    from collections import Counter

    import pytest

    # by default should wrap unsortable (error)
    with pytest.warns(UserWarning, match=r"encountered exception"):
        res = list(select(_mixed_iter_errors(), order_value=lambda o: isinstance(o, datetime)))
    assert Counter(type(t).__name__ for t in res) == Counter({"_A": 4, "_B": 2, "Unsortable": 1})
    # compare the returned error wrapped in the Unsortable
    returned_error = next(o for o in res if isinstance(o, Unsortable)).obj
    assert "Unhandled error!" == str(returned_error)


def test_order_key_unsortable() -> None:

    from collections import Counter

    # both unsortable and items which dont match the order_by (order_key) in this case should be classified unsorted
    res = list(select(_mixed_iter_errors(), order_key="z"))
    assert Counter(type(t).__name__ for t in res) == Counter({"_A": 4, "Unsortable": 3})


def test_order_default_param() -> None:

    # test default, shift items without a datetime to the end using reverse
    epoch_time = datetime.fromtimestamp(0)
    res = list(select(_mixed_iter_errors(), order_value=lambda o: isinstance(o, datetime), default=epoch_time, reverse=True))
    assert len(res) == 7
    # should be at the end, because we specified reverse=True
    assert str(res[-1]) == "Unhandled error!"


def test_no_recursive_unsortables() -> None:

    from collections import Counter

    # select to select as input, wrapping unsortables the first time, second should drop them
    # reverse=True to send errors to the end, so the below order_key works
    res = list(select(_mixed_iter_errors(), order_key="z", reverse=True))
    assert Counter(type(t).__name__ for t in res) == Counter({"_A": 4, "Unsortable": 3})

    # drop_unsorted
    dropped = list(select(res, order_key="z", drop_unsorted=True))
    for o in dropped:
        assert isinstance(o, _A)
    assert len(dropped) == 4

    # wrap_unsorted -- shouldn't recursively wrap Unsortable
    # wrap_unsorted is True by default
    wrapped = list(select(res, order_key="z"))
    assert len(wrapped) == 7

    # make sure other types (exceptions/_B) aren't wrapped twice
    for x in wrapped:
        if isinstance(x, Unsortable):
            assert not isinstance(x.obj, Unsortable)
