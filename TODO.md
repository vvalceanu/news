
P0
// - do a trivial PoC, commit to main: the displayer main page talks to Miniflux and displays the latest articles. On clicking on each of them, it displays its raw body as it was saved by Miniflux.
- implement trivial Haiku call to translate a new batch of titles. This also uses django q2 since it does calls that can take a while. Problem: how do we trigger a sweep?

P1
- put the displayer under Cludflare to minimize attacks, after we implement Sonnet calls.
- make Miniflux not accessible from www, by everyone like so. At least some basic http auth.
- switch to sentry or some other logging

P2
- what do I do long term, we cannot keep calling Haiku for title translation everytime SRF publishes something, we need to find a different way.

