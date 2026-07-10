retry-fetch developer guide
============================

This is a short reference for contributors working on the retry-fetch library itself,
as opposed to the end-user README aimed at consumers of the package.

Running the tests
------------------

Example usage::

.. code-block:: bash

   npm install
   npm test

Design notes
------------

The retry loop itself lives in a single module, deliberately kept free of any
platform-specific fetch polyfill logic so it can be unit tested without a DOM or a
Node.js `fetch` implementation present at all — the loop is given a plain async
function to call and knows nothing about HTTP.

REQ-1: any change to the backoff calculation must include a unit test asserting the
exact delay sequence for at least three consecutive retries, since silent changes to
backoff timing are easy to introduce and hard to notice in review.

TODO: add a property-based test for the backoff calculation once a suitable
dependency-free property-testing approach is agreed on.

Contribution notes
-------------------

- Keep the zero-dependency constraint. This is the library's whole reason to exist.
- WARNING: do not add a build step. The package ships as plain, unbundled JavaScript.
