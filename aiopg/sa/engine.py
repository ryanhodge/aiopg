try:
    from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2
except ImportError:  # pragma: no cover
    raise ImportError('aiopg.sa requires sqlalchemy')

import asyncio
import aiopg

from .connection import SAConnection
from .exc import InvalidRequestError


dialect = PGDialect_psycopg2()
dialect.implicit_returning = True
dialect.supports_native_enum = True
dialect.supports_smallserial = True  # 9.2+
dialect._backslash_escapes = False
dialect.supports_sane_multi_rowcount = True  # psycopg 2.0.9+


@asyncio.coroutine
def create_engine(dsn=None, *, minsize=10, maxsize=10, loop=None,
                  dialect=dialect, **kwargs):
    if loop is None:
        loop = asyncio.get_event_loop()
    pool = yield from aiopg.create_pool(dsn, minsize=minsize, maxsize=maxsize,
                                        loop=loop, **kwargs)
    conn = yield from pool.acquire()
    real_dsn = conn.dsn
    pool.release(conn)
    return Engine(dialect, pool, real_dsn)


class Engine:
    def __init__(self, dialect, pool, dsn):
        self._dialect = dialect
        self._pool = pool
        self._dsn = dsn

    @property
    def dialect(self):
        return self._dialect

    @property
    def name(self):
        return self._dialect.name

    @property
    def driver(self):
        return self._dialect.driver

    @property
    def dsn(self):
        return self._dsn

    @asyncio.coroutine
    def acquire(self):
        raw = yield from self._pool.acquire()
        conn = SAConnection(raw, self._dialect)
        return conn

    def release(self, conn):
        if conn.in_transaction:
            raise InvalidRequestError("Cannot release a connection with "
                                      "not finished transaction")
        raw = conn.connection
        self._pool.release(raw)

    def __enter__(self):
        raise RuntimeError(
            '"yield from" should be used as context manager expression')

    def __exit__(self, *args):
        # This must exist because __enter__ exists, even though that
        # always raises; that's how the with-statement works.
        pass  # pragma: nocover

    def __iter__(self):
        # This is not a coroutine.  It is meant to enable the idiom:
        #
        #     with (yield from pool) as conn:
        #         <block>
        #
        # as an alternative to:
        #
        #     conn = yield from pool.acquire()
        #     try:
        #         <block>
        #     finally:
        #         conn.release()
        conn = yield from self.acquire()
        return _ConnectionContextManager(self, conn)


class _ConnectionContextManager:
    """Context manager.

    This enables the following idiom for acquiring and releasing a
    connection around a block:

        with (yield from pool) as conn:
            cur = yield from conn.cursor()

    while failing loudly when accidentally using:

        with pool:
            <block>
    """

    __slots__ = ('_engine', '_conn')

    def __init__(self, engine, conn):
        self._engine = engine
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *args):
        try:
            self._engine.release(self._conn)
        finally:
            self._engine = None
            self._conn = None
