# Phomymo design format

What `make_link.py` takes and Phomymo's Import accepts. Units are printer dots: 8 per mm (203 DPI).

```json
{
  "name": "Lunch",
  "version": 3,
  "labelSize": { "width": 48, "height": 40, "round": false, "continuous": false },
  "elements": [ ... ]
}
```

`labelSize` is in **mm**. The M110 head is 48 mm (384 dots) wide, so `width` is at most 48 on his roll; a
40 mm tall label is 320 dots. `round: true` clips to a circle.

## Every element

| field | meaning |
|---|---|
| `id` | any unique string |
| `type` | `text`, `shape`, `barcode`, `qr`, `image` |
| `x`, `y` | top-left corner, in dots from the label's top-left |
| `width`, `height` | box size in dots |
| `rotation` | degrees clockwise, around the box centre: the box is laid out unrotated, then turned in place. A vertical line 240 long centred on (248, 184) is `x: 128, y: 182, width: 240, height: 4, rotation: 90` |
| `zone` | `0` (multi-label rolls only use other values) |

Later elements draw on top of earlier ones.

## text

| field | values |
|---|---|
| `text` | `\n` for line breaks; `[[dt\|YYYY-MM-DD]]`, `[[date]]`, `[[time]]` fill at print time |
| `fontSize` | px (dots); 30 is a bold headline on a 40 mm label, 18-20 is small print |
| `fontFamily` | `"Inter, sans-serif"` (his default). Also Roboto, Open Sans, Lato, Montserrat, Oswald, Playfair Display, Merriweather, Roboto Mono, Source Code Pro |
| `fontWeight` | `normal`, `bold` |
| `fontStyle` | `normal`, `italic` |
| `textDecoration` | `none`, `underline` |
| `align` | `left`, `center`, `right` (4-dot inset from the box edge) |
| `verticalAlign` | `top`, `middle`, `bottom` |
| `color` | `black`, `white` (white on a black background or shape) |
| `background` | `transparent`, `white`, `black` |
| `noWrap` | `true`: only `\n` breaks lines, overflow runs past the box |
| `clipOverflow` | `true`: cut text at the box edge |
| `autoScale` | `true`: ignore `fontSize` and use the **largest** size (6-200) at which the text fits the box. It grows short text as well as shrinking long text, and with wrapping on it will wrap a line to get bigger, so pair it with `noWrap: true` for one item per line |

Lines are `fontSize * 1.2` apart and word-wrap at the box width.

## shape

| field | values |
|---|---|
| `shapeType` | `rectangle`, `ellipse`, `triangle`, `line` (horizontal through the box centre; rotate it for other angles) |
| `fill` | `white`, `black`, or a dither grey: `dither-6`, `-12`, `-25`, `-37`, `-50`, `-62`, `-75`, `-87`, `-94` (% grey) |
| `stroke` | `none`, `black`, `white` |
| `strokeWidth` | dots |
| `cornerRadius` | dots, rectangles only |

## barcode

| field | values |
|---|---|
| `barcodeData` | the value; must be valid for the format |
| `barcodeFormat` | `CODE128` (anything), `EAN13` (12-13 digits), `CODE39` |
| `showText` | `true` prints the value under the bars |
| `textFontSize`, `textBold` | readout size (default 12) and weight |

Bars are snapped to whole dots, so a box too narrow for the data shows a red strip in the editor and won't
scan. The preview shows if that happened.

## qr

| field | values |
|---|---|
| `qrData` | the text or URL |

Drawn as a square of `min(width, height)`. About 120 dots (15 mm) scans reliably for a short URL.

## image

| field | values |
|---|---|
| `imageData` | `data:` URL (PNG/JPEG) |
| `naturalWidth`, `naturalHeight` | the image's pixel size |
| `dither` | `floyd-steinberg` (default for photos), `atkinson`, `ordered`, `threshold` |
| `brightness`, `contrast` | -100 to 100 |

Avoid images when you can: they make links tens of KB long. A label with an image is dithered as a
whole, which roughens text edges slightly.
