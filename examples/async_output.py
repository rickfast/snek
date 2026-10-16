from snek import run, checkpoint


async def main() -> None:
    # More than three output chunks; also crosses a multibyte UTF-8 boundary.
    for i in range(4):
        print("λ🐍" * 12000)
        await checkpoint()
    print("done")


run(main())
