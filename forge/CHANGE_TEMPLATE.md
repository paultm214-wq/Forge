Change: <project name, e.g. glassinv>

<!--
A change to a project forge has already finished. Either:

  * drop this file in ~/forge/inbox/ (the first line names the project), or
  * run:  forge change <project-or-run-dir> this-file.md   (no first line needed)

forge copies the project's newest finished run, decides which chunks the
change touches, rebuilds only those - starting from their current code - adds
new chunks for new features, re-tests everything that depends on them, runs
the reviews, and commits to a branch built on the project's last branch. The
original run is never modified.

To start a NEW project from an existing one instead (glass -> garden), write
a complete spec (SPEC_TEMPLATE.md) and put these two lines above its title:

    Derive: glassinv
    New-project: gardeninv

Each chunk then starts from the old project's matching code; chunks that
already pass the new tests are carried over without a model call.

Like a spec, a change request succeeds when it is checkable. Say what is
decided, not just what is wanted - otherwise the planner decides for you.
-->

# <Short title of the change>

## What changes

One or two paragraphs: what the user should see or be able to do afterwards.

## Decisions

The details the planner would otherwise guess:

- Names: new fields, routes, functions, settings (exact spelling)
- Values: thresholds, limits, defaults, units
- Where it shows up: which page, which view, which file
- What it replaces, if anything, and what must keep working unchanged

## Acceptance

Checkable facts with known answers, as in a spec. In the spec forge relabels
them CH<n>-1, CH<n>-2 ... (n counts the project's changes), so they never
collide with an earlier change's, and gives each one to a chunk test:

1. <input> -> <exact expected output>
2. <edge case> -> <expected behaviour>

## Assets (optional)

New images, as a ```json list with the same keys as a spec's Assets section
(plus reference_images and tileable - see SPEC_TEMPLATE.md). They are added
verbatim; existing images are re-rendered only if the change says so.
