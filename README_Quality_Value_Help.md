# 3D Converter — Quality Value Field & Context Tooltip

This update improves the **Значение** field without changing the FFmpeg encoding profiles.

## UI behavior

- The field placeholder changes with the selected quality mode.
- `Авто (Magic)` disables manual entry and says that no value is required.
- Changing the quality mode clears a value entered for the previous mode so that a number is not silently reused with a different unit or meaning.
- The label `Значение ⓘ` shows a tooltip after hovering for a short moment.
- The tooltip is rebuilt when quality mode, encoder, source video, or operation changes.
- For `2D → TS`, the field is disabled because the operation copies streams without re-encoding.

## Encoder/device-aware tooltip content

- **Intel QSV:** describes `global_quality`, its 1–51 range, QSV VBR `extbrc=1`, and QSV CBR rate parameters.
- **NVIDIA NVENC:** describes CQ 15–28, `-rc vbr`, `-rc cbr`, and codec-specific behavior.
- **Auto Magic:** reports the default quality/preset selected for the loaded source (QSV 15/16 or NVENC CQ 19/21 with p6/p5). Before a source is loaded, it explains the selection threshold instead of guessing.
- The app probes the QSV encoder log to display the actual Intel adapter name when FFmpeg provides it (for example, Intel Iris Xe Graphics).
- For target file size, the tooltip shows an example bitrate calculated from the currently loaded video's duration and warns that audio/container overhead is added separately.

## Installation

Replace `main.py` in the project folder and add `quality_value_help.py` next to it. Keep the project's existing `encoding_profiles.py`, `output_path_utils.py`, `preset_options.py`, `requirements.txt`, and `reference_bat/` folder.

Run as usual:

```cmd
python main.py
```

Run the project's tests with:

```cmd
python -m unittest -v
```

Automated checks cover tooltip text, encoder-specific ranges and parameters, Auto Magic values, target-size estimate, and the existing conversion profiles.
