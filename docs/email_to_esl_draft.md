# DRAFT: user sends

Not sent. Review, adjust, replace the placeholders, and send it yourself after the first public upload. Do not attach any
defect report or patch; those need your separate decision.

To: the 3D-ICE maintainers (use the contact listed on https://github.com/esl-epfl/3d-ice or https://esl.epfl.ch/3d-ice; the
README names `3d-ice@listes.epfl.ch`, a mailing list that needs a subscription, so a named maintainer is better if one is listed)

Subject: Unofficial pip package of 3D-ICE 4.0 (pinned build) - heads-up

Dear 3D-ICE team,

I use 3D-ICE as the reference solver in my research on thermal surrogate models for 3D ICs. To make my results reproducible
I built a pip package of 3D-ICE-Emulator and have published it as an unofficial redistribution. I wanted you to hear about it from
me, and to ask whether you are happy with it.

What it is:

* `pip install threedice` installs one compiled binary, 3D-ICE-Emulator, built from esl-epfl/3d-ice at commit
  4953952a1ef6d38807ff307212a6f15e5b2ef935 (3D-ICE 4.0 plus later commits). Two build-only patches are applied (drop -Werror;
  `LUIndex_t` from long to int to match SuperLU_MT); neither changes the numerics. The SystemC wrapper, the server/client and the
  heat-sink plugin are not included.
* It is licensed GPL-3.0-or-later, like 3D-ICE. The source distribution contains the complete corresponding source: the unmodified
  3D-ICE tree at that commit, the SuperLU_MT tree, and the patches.
* The README, the NOTICE file and the PyPI description say clearly that it is an unofficial redistribution, not affiliated with or
  endorsed by EPFL ESL. They link to your repository, credit the authors, and ask users to cite the four 3D-ICE papers (DATE 2026,
  TCAD 2021, IEEE Transactions on Computers 2014, ICCAD 2010). I am listed only as the packager.
* Repository with the packaging and build workflows: https://github.com/rajul-kk/threedice   [placeholder: confirm the final URL]
  Package: https://pypi.org/project/threedice/   [placeholder: add once published]

One detail you may want to know: 3D-ICE's tree ships `superlu_mt-4.0.0.zip`, but the build I pinned (and compared against) linked a
git checkout of SuperLU_MT master (commit e60ed2c1dd491e1832d783f519c1851288f9d422, 2026-03-29), which fixes the 64-bit-integer
prototype mismatches of the 4.0.0 zip. The package reproduces that tree, not the zip, and says so in its README. On nine stored
steady-state solves it gave bit-identical float32 results to my reference build.

I am glad to do whatever suits you best: transfer the repository and the PyPI project to you or to an ESL account, rename the package
(for example to make its unofficial status more obvious), change the wording, or take it down entirely. Just let me know. I would also
be happy to contribute the packaging files upstream if you would prefer to publish an official wheel yourselves.

Thank you for making 3D-ICE available.

Best regards,
Rajul Kabeer
rajul.kabeer@gmail.com
