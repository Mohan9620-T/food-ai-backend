/** Identify requests that need the file pipeline instead of a text-only reply. */
export function isDocumentFileRequest(message: string): boolean {
  if (isDocumentCapabilityQuestion(message)) return false;
  // Quoted source content must not supply action words or output formats.
  const instruction = message.replace(/"[^"]*"|“[^”]*”|`[^`]*`/g, ' ');
  const action =
    /\b(?:create|crate|creat|generate|make|prepare|build|draft|write|produce|convert|export|save|update|modify|edit|add|insert|replace|remove|delete|format|split|merge|combine|filter|transform|expand)\b/i;
  const document =
    /\b(?:documents?|documet|files?|pdf|word|docx|excel|xlsx|csv|powerpoint|pptx|presentations?|slides?|spreadsheet|workbook|brochure|report|txt|markdown)\b/i;
  const delivery = instruction.match(/\b(?:give|send|download)\b(.*)/i);
  return (
    (action.test(instruction) && document.test(instruction)) ||
    Boolean(delivery && document.test(delivery[1]))
  );
}

export function isDocumentCapabilityQuestion(message: string): boolean {
  return (
    message.length < 300 &&
    /\b(?:documents?|documet|files?)\b/i.test(message) &&
    /\b(?:what|which)\b.*\b(?:types?|formats?|kinds?)\b.*\b(?:create|generate)\b|\b(?:create|generate)\b.*\b(?:what|which)\b.*\b(?:types?|formats?|kinds?)\b|\bwhy\b.*\b(?:not|cannot|can't|shouldn't|unable)\b.*\b(?:create|generate)\b/i.test(
      message,
    )
  );
}
