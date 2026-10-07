/**
 * Offers text as a file download, straight from the browser - no second request to the server.
 *
 * A temporary object URL behind a hidden link with "download": the browser saves instead of
 * navigating. The URL is released after the click has been handed to the browser.
 */
export function saveText(text: string, filename: string, mediaType: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: mediaType }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url));
}
