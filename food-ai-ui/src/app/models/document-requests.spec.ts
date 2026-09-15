import { isDocumentFileRequest } from './document-requests';

describe('Document file requests', () => {
  it.each([
    'can you create the word document on this content',
    'give me this content as a Word document',
    'export the PDF to DOCX',
    'write a PowerPoint presentation',
    'create a CSV file',
    'how many American president on 1900 to 2026 list out and crate the excel file',
    'creat an Excel file with the planets',
    'can you add this content "My design focuses on text" and update the excel document',
  ])('routes the file request: %s', (instruction) => {
    expect(isDocumentFileRequest(instruction)).toBe(true);
  });

  it.each([
    'can you read this document and tell me what it is about',
    'Read this PDF end to end and give me a summary',
    'What does "create a Word file" mean?',
    'Give me a recipe for dinner',
    'can you create which type of documet',
    "why you shouldn't generate or create actual file",
    'What types of document files can you generate?',
  ])('keeps a reading/chat request out of file creation: %s', (instruction) => {
    expect(isDocumentFileRequest(instruction)).toBe(false);
  });
});
