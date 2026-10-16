# error: non-empty format specs on list[i32] need !s or !r
def main() -> None:
    print(f"{[1]:>10}")
