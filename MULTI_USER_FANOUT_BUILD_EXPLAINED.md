# Multi-user trade fan-out, built 2026-09-03

Not committed -- working notes, plain-language explanation of what
piece 1, piece 1.5, and piece 2 actually built. Full technical detail
lives in `MULTI_USER_FANOUT_PLAN.md` (the original design, plus a
"Build notes" section covering what changed during implementation);
this doc is the explanation, not a replacement for it.

## The problem this solves

Before this: the app could only ever really trade for one hardcoded
account. Any other user who signed up and connected a broker was fully
set up -- their own MT5 terminal, their own bridge connection, all of
it working -- but nothing ever traded for them. The detection engine
simply didn't know they existed.

## What's built now (not live yet -- see "Status" below)

The engine now watches **any number of real accounts at once**. When it
finds a setup, it asks "who currently has this model turned on, with a
working broker connection right now?" and fires that same trade into
*every one* of those accounts, each sized to their own balance, each
managed from that point on completely independently of the others.

## The three things that had to be true for this to be safe, not just working

**1. Real orders fan out correctly, and one broken connection can't
take anyone else down.** Each subscriber gets their own order
placement, fill tracking, and target-setting. If one person's bridge
goes down mid-poll, everyone else keeps trading normally.

**2. Overnight risk management now covers everyone, not just one
account.** This was the one that almost got missed. There's a separate
piece of the system that watches a position *after* it fills --
halving its size if it's still open at 5pm, and tracking it across
multiple days until it naturally closes. That piece was never mentioned
in the original design for this. Left alone, the fix above would have
looked completely done -- real orders firing for everyone -- while
overnight risk management silently kept working for only the one
original account. That's exactly the shape of bug this project already
got bitten by twice this month (a real position left unmanaged,
discovered a week later). It's now fixed: every subscriber gets their
own copy of that overnight watcher, built the moment they connect a
bridge with the model turned on -- no restart required.

**3. Bookkeeping stays honest for everyone, including nobody.** Each
subscriber who actually gets a real fill gets their own trade record.
Separately, the app still keeps a "here's what this model would have
done today" record every single day, even if literally nobody is live
on it yet -- that's how a brand-new model gets proven safe before
anyone risks real money on it, and it already worked this way before
today. Making that keep working, once there's no more single "the
account" to credit it to, needed its own small piece of surgery (a
migration, so that one shared record doesn't have to falsely belong to
one specific person).

## What was actually hard about this

Not the "fan a trade out to N accounts" part -- that turned out to be
fairly mechanical. The hard part was finding the two things the
original plan quietly assumed would keep working on their own and
wouldn't have: the overnight-risk piece, and what happens to the
model's own practice-trading record once there's no more one hardcoded
person to own it. Both were caught and discussed before writing any
code for them, not discovered afterward.

## Status

Built, tested (over a dozen new tests), on `main`. **Nothing has been
deployed.** The real live account is still running the old, one-account
code, completely untouched -- this work has had zero effect on live
trading so far. Deploying it, and later moving the real account onto
it, are both separate, deliberate steps for later.
