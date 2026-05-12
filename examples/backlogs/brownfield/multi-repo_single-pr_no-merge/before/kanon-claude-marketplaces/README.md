# `kanon-claude-marketplaces/` -- target repo placeholder

In a live run of this example, this directory is a **symlink** to a checkout
of `caylent/kanon-claude-marketplaces` (a sibling catalog repo audited
read-only by epic E13). DevBench reads `backlog/config/devbench.yaml`, sees
`caylent/kanon-claude-marketplaces` in the `repos:` map with
`checkout_directory: kanon-claude-marketplaces`, and operates against this
path.

The example ships an empty placeholder directory so the backlog and the spec
can be reviewed without cloning the underlying repo. To actually execute the
backlog against the real repo:

```bash
cd <your-workspace-root>
rm -rf kanon-claude-marketplaces
git clone git@github.com:caylent/kanon-claude-marketplaces.git kanon-claude-marketplaces
# or, if the repo is already cloned elsewhere as a sibling:
# ln -s ../kanon-claude-marketplaces kanon-claude-marketplaces
```

Then launch DevBench with the commands in `devbench-commands.txt`.
