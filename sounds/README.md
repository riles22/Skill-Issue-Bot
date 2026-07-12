# Sounds

These `.dca` files (pre-encoded Discord Opus audio) are the original sound set
of **Airhorn Solutions** (airhornbot), taken from the `golang` branch of
[discord/airhornbot](https://github.com/discord/airhornbot), which is released
under the MIT License:

> The MIT License (MIT)
>
> Copyright (c) 2016 Hammer and Chisel
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to
> deal in the Software without restriction, including without limitation the
> rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
> sell copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in
> all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
> FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
> IN THE SOFTWARE.

## File format

A `.dca` file is a stream of Opus audio frames, each prefixed with a 16-bit
little-endian length. `DCASource` in `app.py` feeds these frames straight to
Discord with no re-encoding, so playing them requires neither ffmpeg nor an
Opus encoder.
