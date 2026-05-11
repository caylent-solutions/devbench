# `kanon/` -- target repo placeholder

In a live run of this example, this directory is a **symlink** to a checkout of
`caylent-solutions/kanon` (the open-source kanon CLI). DevBench reads
`backlog/config/devbench.yaml`, sees `caylent-solutions/kanon` in the `repos:`
map with `checkout_directory: kanon`, and operates against this path.

The example ships an empty placeholder directory so the backlog and the spec
can be reviewed without cloning the underlying repo. To actually execute the
backlog against the real repo:

```bash
cd <your-workspace-root>
rm -rf kanon
git clone git@github.com:caylent-solutions/kanon.git kanon
# or, if the repo is already cloned elsewhere as a sibling:
# ln -s ../kanon kanon
```

Then launch DevBench with the commands in `devbench-commands.txt`.
