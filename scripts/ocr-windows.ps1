$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]

$convertTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name.StartsWith('IAsyncOperation')
} | Select-Object -First 1

function Complete-OcrOperation($operation, [Type]$type) {
    $task = $convertTask.MakeGenericMethod($type).Invoke($null, @($operation))
    $task.GetAwaiter().GetResult()
}

$file = Complete-OcrOperation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:M_FLOW_OCR_IMAGE)) ([Windows.Storage.StorageFile])
$stream = Complete-OcrOperation ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
try {
    $decoder = Complete-OcrOperation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Complete-OcrOperation ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Ignore)) ([Windows.Graphics.Imaging.SoftwareBitmap])
    try {
        $preferred = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | Where-Object LanguageTag -Like 'ru*' | Select-Object -First 1
        $engine = if ($preferred) { [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($preferred) } else {
            [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
        }
        if (-not $engine) { exit 1 }
        $result = Complete-OcrOperation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        [Console]::WriteLine($result.Text)
    } finally {
        $bitmap.Dispose()
    }
} finally {
    $stream.Dispose()
}
