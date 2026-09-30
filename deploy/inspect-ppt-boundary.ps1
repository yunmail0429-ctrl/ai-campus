$ErrorActionPreference = 'Stop'
$deckPath = (Resolve-Path 'local-data/ppt-boundary-validation.pptx').Path
$renderDir = Join-Path (Resolve-Path local-data).Path 'ppt-boundary-review'
New-Item -ItemType Directory -Force -Path $renderDir | Out-Null
$pptApp = New-Object -ComObject PowerPoint.Application
$reviewDeck = $null
try {
    $reviewDeck = $pptApp.Presentations.Open($deckPath, -1, 0, 0)
    $reviewDeck.Export($renderDir, 'PNG', 1600, 900)
    $rows = foreach ($slide in $reviewDeck.Slides) {
        foreach ($shape in $slide.Shapes) {
            if ($shape.HasTextFrame -eq -1) {
                [pscustomobject]@{
                    Height = $shape.Height
                    TextHeight = $shape.TextFrame2.TextRange.BoundHeight
                    Width = $shape.Width
                    TextWidth = $shape.TextFrame2.TextRange.BoundWidth
                }
            }
        }
    }
    $rows | ConvertTo-Json | Set-Content -LiteralPath 'docs/PPT-BOUNDARY-TEST.json' -Encoding utf8
    $rows | Format-Table
} finally {
    if ($reviewDeck) { $reviewDeck.Close() }
    $pptApp.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($pptApp) | Out-Null
}
