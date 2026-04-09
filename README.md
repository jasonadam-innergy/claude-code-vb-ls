# Description
This is a simple Visual Basic Language Server intended for use with Claude Code.
This plugin relies on a [forked version](https://github.com/jasonadam-innergy/visualbasic-language-server) of [visualbasic-language-server](https://github.com/CoolCoderSuper/visualbasic-language-server) by CoolCoderSuper.

[The forked language server can be found here.](https://github.com/jasonadam-innergy/visualbasic-language-server)

Make sure to update the appsetting.json within the scripts folder to point to your correct directories.

# Installation
Run Claude Code and navigate to the plugins page with `/plugin` or through the GUI

Add the marketplace `jasonadam-innergy/claude-code-vb-ls`

Install the `vb-ls` plugin

Update your `appsettings.json` located in the `~/.claude/plugins/cache/claude-code-vb-ls/visual-basic-language-server/1.0.0/scripts` to have your correct build config, build platform, and project directory.

Reload the plugins with `/reload-plugins`

Test the Language Server by telling Claude to `Use the language server to find how many times X function in Y.vb is called`
