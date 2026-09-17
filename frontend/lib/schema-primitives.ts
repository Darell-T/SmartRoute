import { z } from "zod";

export const MAX_TEXT = 300;
export const MAX_LIST = 256;
export const limitedText = (max = MAX_TEXT) => z.string().max(max);
export const nonEmptyText = (max = MAX_TEXT) =>
  limitedText(max).refine((value) => value.trim().length > 0);
export const boundedNumber = (minimum: number, maximum: number) =>
  z.number().finite().min(minimum).max(maximum);
export const boundedInteger = (minimum = 0, maximum = MAX_LIST) =>
  z.number().int().min(minimum).max(maximum);
export const nonEmptyTextList = (maximum = MAX_LIST) =>
  z.array(nonEmptyText()).max(maximum);
