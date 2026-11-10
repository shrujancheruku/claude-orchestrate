# Pricing the GraphQL queries `board` and `threads` build

The GraphQL cost test does not price the queries that `scripts/board` and `scripts/threads` build by concatenation. Instead it forbids any connection size (`first:` or `last:`) in any string literal of those two scripts, so none of their queries can size a connection at all.

## Why this is out of scope

Every other call site sends a named `*_QUERY` constant, and the test prices those directly. `board` and `threads` assemble their queries from pieces, which the `query={NAME}` scan cannot see. There are two ways to keep them inside the rate-limit budget:

- price whatever they build, which means running or reconstructing the builders inside the test and estimating the cost of the result;
- ban connection sizes in those scripts, so the only queries they can build are ones whose cost is already fixed.

The ban is a few lines with no false negatives: a size hidden in a plain string, an f-string or a concatenated piece is still caught. A pricer for built queries would be a second cost model to maintain next to the constant-based one, and it would guard a case no script has today. The ban is stricter than it needs to be, but it errs on the side of the rule it protects (no unpriced connection sizes).

If either script ever needs a sized connection, the right move is to make that query a named `*_QUERY` constant the existing test already prices, not to teach the test to price built strings.

## Prior requests

- #564: "price the queries board and threads build instead of banning first:/last: in their strings"
