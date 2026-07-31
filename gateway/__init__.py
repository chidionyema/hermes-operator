"""Package marker for ``gateway.operator_shell``.

Deliberately minimal. The hermes-agent monorepo ships a ``gateway/__init__.py``
that re-exports ``GatewayConfig`` / ``SessionStore`` / ``DeliveryRouter`` from
``gateway.config``, ``gateway.session`` and ``gateway.delivery`` -- none of
which this package contains or needs. Inheriting that file made every import
here fail with ``ModuleNotFoundError: No module named 'gateway.config'`` the
moment the monorepo was not also on sys.path.

Nothing in ``operator_shell`` imports the ``gateway`` namespace itself (only
``gateway.operator_shell.*``, plus an optional ``gateway.run`` that is already
guarded by try/except), so an empty marker is the correct standalone form.

The import path is kept as ``gateway.operator_shell`` rather than renamed so
that the monorepo and this package remain drop-in interchangeable.
"""
