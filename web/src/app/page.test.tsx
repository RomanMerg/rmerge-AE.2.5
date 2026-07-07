import { render, screen } from "@testing-library/react";
import Home from "./page";

test("home page renders the app title", () => {
  render(<Home />);
  expect(
    screen.getByRole("heading", { name: /automate this/i }),
  ).toBeInTheDocument();
});
