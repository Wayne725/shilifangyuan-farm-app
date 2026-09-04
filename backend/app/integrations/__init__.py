"""External service adapters.

Import adapters from their defining modules. Keeping this package initializer
side-effect free prevents unrelated SDKs from loading during focused jobs such
as invoice reconciliation and contract tests.
"""
