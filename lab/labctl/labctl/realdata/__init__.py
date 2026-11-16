"""A consistent, masked slice of a real database (Prod Echo I-10, I-13 to I-17).

`inventory.pull` reads the schema, `closure.extract` takes the slice, `mask.mask` masks it with
a key from the environment, `mask.sanitize` checks the result before it is loaded.
"""
