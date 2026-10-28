import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@text-to-cad/ui/primitives/select";
import { Slider } from "@text-to-cad/ui/primitives/slider";
import { TooltipHint } from "@text-to-cad/ui/primitives/tooltip";
import { cn } from "@text-to-cad/ui/utils";
import { english } from "../../../i18n/core.js";
import {
  FILE_SHEET_PRECISION_SLIDER_CLASSES,
  FILE_SHEET_SELECT_TRIGGER_CLASSES,
  FileSheetSettingRow,
  FileSheetSliderField,
  parseFileSheetNumberInput
} from "../../kit/inspector/FileSheet.js";

const TARGET_SLIDER_STEPS = 1000;

// A value box wide enough for the whole of a multiple: its characters, the padding (0.75rem) and the box's
// 1px borders, and a character's room more for the "×", wider than a figure ("×6000"); a large exaggeration
// ("×9500000", "×120000000") takes the box its length needs, so its last figures are never cut off. It
// overrides the sheet's own width, which a joint's figure fits.
const valueInputWidth = (value) => {
  const length = String(value ?? "").length;
  if (length <= 5) return "w-[calc(6ch+0.75rem+2px)]";
  if (length <= 7) return "w-[calc(8ch+0.75rem+2px)]";
  if (length <= 9) return "w-[calc(10ch+0.75rem+2px)]";
  if (length <= 11) return "w-[calc(12ch+0.75rem+2px)]";
  return "w-[calc(14ch+0.75rem+2px)]";
};

/** A slider's step from its range: about a thousand steps across it, a power of ten, never finer than a millionth. */
export function controlStep(control) {
  const range = Math.abs(Number(control.max) - Number(control.min));
  if (!(range > 0)) return 0.01;
  return Number(Math.max(10 ** Math.floor(Math.log10(range / TARGET_SLIDER_STEPS)), 0.000001).toPrecision(12));
}

/**
 * One of Study's What you see controls (`feaControls`: a number or a choice) as its settings-ui.md row: a
 * number is a slider with its committed value (`FileSheetSliderField`), a choice a select (as Preview's
 * settings draw one), full width with no label where its options say what it is (`hideLabel`).
 * A plain function, not a component: the panel's rows are its own elements, in its own order.
 *
 * @param {{ control: object, value: unknown, onChange(value: unknown): void, valueText(value: number, control: object): string }} props
 *   `control.ariaLabel`: the accessible name, where it differs from its label; `control.step`: a slider's increment,
 *   otherwise about a thousandth of its range.
 */
export function controlRow({ control, value, onChange, valueText, t = english }) {
  const label = control.label;
  const name = control.ariaLabel || label;
  if (control.type === "enum") {
    const select = <Select value={String(value ?? "")} onValueChange={(next) => onChange(next)}>
      <TooltipHint content={control.hideLabel ? name : undefined}>
        <SelectTrigger aria-label={name} size="sm" className={cn(FILE_SHEET_SELECT_TRIGGER_CLASSES, "!h-6 w-full min-w-0")}><SelectValue /></SelectTrigger>
      </TooltipHint>
      <SelectContent>{(control.options || []).map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectContent>
    </Select>;
    return control.hideLabel
      ? <div key={control.id} className="min-w-0 px-2" data-fea-control={control.id}>{select}</div>
      : <FileSheetSettingRow key={control.id} label={label}>{select}</FileSheetSettingRow>;
  }
  const text = valueText(value, control);
  return (
    <FileSheetSliderField
      key={control.id}
      label={label}
      labelTitle={control.labelTitle || label}
      value={text}
      onValueCommit={(draft) => onChange(parseFileSheetNumberInput(draft, { fallback: value, min: control.min, max: control.max }))}
      valueInputProps={{ ariaLabel: t("fea.controlValue", { name }), className: valueInputWidth(text) }}
    >
      <Slider
        className={FILE_SHEET_PRECISION_SLIDER_CLASSES}
        value={[Number(value) || 0]}
        min={control.min}
        max={control.max}
        step={control.step ?? controlStep(control)}
        onValueChange={(next) => onChange(next?.[0] ?? value)}
        thumbProps={{ "aria-label": name }}
      />
    </FileSheetSliderField>
  );
}
