# `caylent-private-kanon/` -- target repo placeholder

In a live run of this example, this directory is a **symlink** to a checkout
of `caylent/caylent-private-kanon` (the private manifest repo holding
`*-marketplace.xml` entries). DevBench reads `backlog/config/devbench.yaml`,
sees `caylent/caylent-private-kanon` in the `repos:` map with
`checkout_directory: caylent-private-kanon`, and operates against this path.

The example ships an empty placeholder directory so the backlog and the spec
can be reviewed without cloning the underlying repo. To actually execute the
backlog against the real repo:

```bash
cd <your-workspace-root>
rm -rf caylent-private-kanon
git clone git@github.com:caylent/caylent-private-kanon.git caylent-private-kanon
# or, if the repo is already cloned elsewhere as a sibling:
# ln -s ../caylent-private-kanon caylent-private-kanon
```

Then launch DevBench with the commands in `devbench-commands.txt`.
