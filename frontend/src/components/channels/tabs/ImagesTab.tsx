import { Controller, useWatch, type UseFormReturn } from "react-hook-form";

import type { ChannelFormValues } from "../../../lib/channelForm";
import {
  DEFAULT_MOTION_STYLE,
  DEFAULT_TRANSITION_PACK,
  IMAGE_TOOLS,
  MOTION_STYLE_DESCRIPTIONS,
  MOTION_STYLE_LABELS,
  MOTION_STYLES,
  ON_IMAGE_TEXT_OPTIONS,
  TRANSITION_PACK_DESCRIPTIONS,
  TRANSITION_PACK_LABELS,
  TRANSITION_PACKS,
  type ImageTool,
  type OnImageText,
} from "../../../types";
import { NumberField } from "../../form/NumberField";
import { TransitionPackPreview } from "../../storyboard/TransitionPackPreview";
import { Field, FieldGrid, FormSection } from "../../ui/Field";
import { Input, Select, Textarea } from "../../ui/Input";
import { KnownKeysDatalist } from "./VoiceTab";

const IMAGE_TOOL_LABELS: Record<ImageTool, string> = {
  google_gemini: "Google Gemini",
  openai: "OpenAI",
  flux_bfl: "FLUX (Black Forest Labs)",
  flux_fal: "FLUX via fal.ai",
  flux_replicate: "FLUX via Replicate",
  ideogram: "Ideogram",
  recraft: "Recraft",
  leonardo: "Leonardo",
  local: "Local (on this computer)",
  manual: "Manual (I add images myself)",
  other: "Other",
};

const ON_IMAGE_TEXT_LABELS: Record<OnImageText, string> = {
  app_popups: "The app draws popups and callouts (recommended)",
  none: "No text on images",
};

export function ImagesTab({ form }: { form: UseFormReturn<ChannelFormValues> }) {
  const {
    register,
    control,
    formState: { errors },
  } = form;
  const e = errors.images;
  const pack = useWatch({ control, name: "images.transition_pack" }) ?? DEFAULT_TRANSITION_PACK;
  const motionStyle = useWatch({ control, name: "images.motion_style" }) ?? DEFAULT_MOTION_STYLE;

  return (
    <div className="flex flex-col gap-10">
      <FormSection title="Image tool" description="Which service draws the scenes, and how.">
        <FieldGrid>
          <Field label="Tool" htmlFor="images.tool">
            <Select id="images.tool" {...register("images.tool")}>
              {IMAGE_TOOLS.map((tool) => (
                <option key={tool} value={tool}>
                  {IMAGE_TOOL_LABELS[tool]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Model" htmlFor="images.model" hint="The tool's model name, if it has more than one.">
            <Input id="images.model" placeholder="gemini-2.5-flash-image" spellCheck={false} {...register("images.model")} />
          </Field>
          <Field label="Style guide" htmlFor="images.style_guide" className="md:col-span-2" hint="Added to every scene prompt. Describe the look you want.">
            <Textarea id="images.style_guide" rows={3} placeholder="Cinematic photo-realism, soft natural light, muted warm palette, shallow depth of field, no text, no logos, no real public figures" {...register("images.style_guide")} />
          </Field>
          <Field label="Things to avoid" htmlFor="images.negative_rules" className="md:col-span-2">
            <Textarea id="images.negative_rules" rows={2} placeholder="no watermarks, no distorted hands, no extra fingers, no gibberish text" {...register("images.negative_rules")} />
          </Field>
        </FieldGrid>
      </FormSection>

      <FormSection title="Size and references">
        <FieldGrid columns={3}>
          <Field label="Aspect for long videos" htmlFor="images.aspect_long">
            <Input id="images.aspect_long" placeholder="16:9" spellCheck={false} {...register("images.aspect_long")} />
          </Field>
          <Field label="Aspect for Shorts" htmlFor="images.aspect_shorts">
            <Input id="images.aspect_shorts" placeholder="9:16" spellCheck={false} {...register("images.aspect_shorts")} />
          </Field>
          <Field label="Resolution" htmlFor="images.resolution">
            <Input id="images.resolution" placeholder="1920x1080" spellCheck={false} {...register("images.resolution")} />
          </Field>
          <Field label="Reference images folder" htmlFor="images.reference_folder" className="md:col-span-3" hint="Example images the tool should match, when it supports references.">
            <Input id="images.reference_folder" placeholder="D:\CashCowStudio\refs\kind-ledger" spellCheck={false} {...register("images.reference_folder")} />
          </Field>
        </FieldGrid>
      </FormSection>

      <FormSection title="Text on images" description="Images are made without text. Popups, callouts and arrows are drawn by the app so they stay editable.">
        <FieldGrid>
          <Field label="On-image text" htmlFor="images.on_image_text">
            <Select id="images.on_image_text" {...register("images.on_image_text")}>
              {ON_IMAGE_TEXT_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {ON_IMAGE_TEXT_LABELS[option]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Popup style" htmlFor="images.popup_style">
            <Input id="images.popup_style" placeholder="Rounded box, brand yellow, bold Arial 64px, slide-in from left" {...register("images.popup_style")} />
          </Field>
        </FieldGrid>
      </FormSection>

      <FormSection
        title="Motion and transitions"
        description="How the camera moves over each picture and how one picture changes into the next. The storyboard AI picks transitions from the chosen pack; a reviewer can still pick any transition by hand."
      >
        <FieldGrid>
          <Field label="Transition pack" htmlFor="images.transition_pack" hint={TRANSITION_PACK_DESCRIPTIONS[pack]}>
            <Select id="images.transition_pack" {...register("images.transition_pack")}>
              {TRANSITION_PACKS.map((option) => (
                <option key={option} value={option}>
                  {TRANSITION_PACK_LABELS[option]}
                  {option === DEFAULT_TRANSITION_PACK ? " (default)" : ""}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Camera motion" htmlFor="images.motion_style" hint={MOTION_STYLE_DESCRIPTIONS[motionStyle]}>
            <Select id="images.motion_style" {...register("images.motion_style")}>
              {MOTION_STYLES.map((option) => (
                <option key={option} value={option}>
                  {MOTION_STYLE_LABELS[option]}
                  {option === DEFAULT_MOTION_STYLE ? " (default)" : ""}
                </option>
              ))}
            </Select>
          </Field>
        </FieldGrid>
        <TransitionPackPreview pack={pack} />
      </FormSection>

      <FormSection title="Account" description="Keys themselves are entered once in Settings. Here you only name the key to use.">
        <FieldGrid>
          <Field label="API key name" htmlFor="images.api_key_env" error={e?.api_key_env?.message} hint="The key's name in Settings, like GEMINI_API_KEY. Never the key itself.">
            <Input id="images.api_key_env" list="known-keys-images" className="font-mono" placeholder="GEMINI_API_KEY" spellCheck={false} autoComplete="off" invalid={Boolean(e?.api_key_env)} {...register("images.api_key_env")} />
            <KnownKeysDatalist id="known-keys-images" />
          </Field>
          <Field label="Monthly budget (images)" htmlFor="images.monthly_budget_images" error={e?.monthly_budget_images?.message} hint="Optional. The app warns when the channel gets close.">
            <Controller
              control={control}
              name="images.monthly_budget_images"
              render={({ field, fieldState }) => (
                <NumberField id="images.monthly_budget_images" min={0} step={100} placeholder="2000" value={field.value} onChange={field.onChange} onBlur={field.onBlur} invalid={Boolean(fieldState.error)} />
              )}
            />
          </Field>
        </FieldGrid>
      </FormSection>
    </div>
  );
}
