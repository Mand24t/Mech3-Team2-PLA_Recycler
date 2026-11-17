param(
    [string]$Source = (Join-Path $PSScriptRoot '..\..\assets\Glossy Blue Ribbon Poop Emoji.png'),
    [string]$Output = (Join-Path $PSScriptRoot '..\..\assets\app_icon.ico')
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$sourceImage = [System.Drawing.Image]::FromFile($Source)
$entries = @()
try {
    foreach ($size in @(16, 20, 24, 28, 32, 40, 48, 56, 64, 80, 96, 128, 192, 256)) {
        $bitmap = New-Object System.Drawing.Bitmap($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $memory = New-Object System.IO.MemoryStream
        try {
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.DrawImage($sourceImage, 0, 0, $size, $size)
            # Preserve RGBA directly. GetHicon/Icon.Save corrupts alpha in this environment.
            $bitmap.Save($memory, [System.Drawing.Imaging.ImageFormat]::Png)
            $entries += [pscustomobject]@{ Size = $size; Payload = $memory.ToArray() }
        } finally {
            $memory.Dispose()
            $graphics.Dispose()
            $bitmap.Dispose()
        }
    }
    $stream = [System.IO.File]::Create($Output)
    $writer = New-Object System.IO.BinaryWriter($stream)
    try {
        $writer.Write([uint16]0)
        $writer.Write([uint16]1)
        $writer.Write([uint16]$entries.Count)
        $offset = 6 + 16 * $entries.Count
        foreach ($entry in $entries) {
            $writer.Write([byte]($entry.Size % 256))
            $writer.Write([byte]($entry.Size % 256))
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]32)
            $writer.Write([uint32]$entry.Payload.Length)
            $writer.Write([uint32]$offset)
            $offset += $entry.Payload.Length
        }
        foreach ($entry in $entries) { $writer.Write([byte[]]$entry.Payload) }
    } finally { $writer.Dispose() }
} finally { $sourceImage.Dispose() }
