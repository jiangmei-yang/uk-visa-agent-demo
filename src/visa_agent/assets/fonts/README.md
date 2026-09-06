# Bundled Noto Sans SC

`NotoSansSC-Regular.ttf` is a static weight-400 instance of the Noto Sans SC
variable font from the Google Fonts repository, commit
`a85815a42757630ce188fdad368c2dfc444d4773`, path `ofl/notosanssc/NotoSansSC[wght].ttf`.

Source URL:
https://raw.githubusercontent.com/google/fonts/a85815a42757630ce188fdad368c2dfc444d4773/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf

Source SHA-256: `a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da`.
Bundled static SHA-256: `eeb06b8a64fd04a2744d95579db1571b51027cda61ed78c62e4b730791525461`.

Reproduction command (FontTools is a build tool, not a runtime dependency):

```sh
uvx --from fonttools==4.59.2 fonttools varLib.instancer NotoSansSC-variable.ttf \
  wght=400 --update-name-table --no-recalc-timestamp --output NotoSansSC-Regular.ttf
```

The font remains under SIL Open Font License 1.1, not this repository's MIT
license. The complete upstream copyright and license are in `OFL-NotoSansSC.txt`.
The reserved name in that notice is "Source"; this instance uses the Noto name.
No endorsement by the font authors is implied.

ReportLab embeds the used glyph subset. ASCII/Western-only existing PDFs keep
their original Helvetica rendering. Other text is checked against the bundled
font's character map; missing glyphs raise an error instead of silent squares.
This font is not claimed to cover every language or every Unicode character.
