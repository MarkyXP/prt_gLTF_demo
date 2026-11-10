"""Decode Int32CDP2 bitlength packets from their MSB-first code text."""


class _BitReader:
    """Read a bounded MSB-first bit stream stored in U32 words."""

    def __init__(self, words, bit_count):
        """Create a reader over one code-text packet.

        > example
        > _BitReader([0x80000000], 1).read_unsigned(1) -> 1
        """
        self.words = words
        self.bit_count = bit_count
        self.position = 0

    def read_unsigned(self, width):
        """Read one unsigned integer field.

        > example
        > _BitReader([0xA0000000], 3).read_unsigned(3) -> 5
        """
        if width < 0 or self.position + width > self.bit_count:
            raise ValueError("bitlength packet ended before all symbols decoded")
        value = 0
        for _index in range(width):
            word = self.words[self.position >> 5]
            value = (value << 1) | ((word >> (31 - (self.position & 31))) & 1)
            self.position += 1
        return value

    def read_signed(self, width):
        """Read and sign-extend one integer field.

        > example
        > _BitReader([0x80000000], 1).read_signed(1) -> -1
        """
        value = self.read_unsigned(width)
        if width and value & (1 << (width - 1)):
            value -= 1 << width
        return value


def decode_bitlength2(count, words, bit_count):
    """Decode an Int32CDP2 fixed-width or run-width bitlength packet.

    > example
    > decode_bitlength2(1, [0x020D0000], 16) -> [0]
    """
    reader = _BitReader(words, bit_count)
    decoded = []
    if reader.read_unsigned(1) == 0:
        minimum_width = reader.read_unsigned(6)
        maximum_width = reader.read_unsigned(6)
        minimum = reader.read_signed(minimum_width)
        maximum = reader.read_signed(maximum_width)
        width = abs(maximum - minimum).bit_length()
        while reader.position < bit_count or len(decoded) < count:
            decoded.append(reader.read_unsigned(width) + minimum)
    else:
        mean = reader.read_signed(32)
        block_value_bits = reader.read_unsigned(3)
        block_length_bits = reader.read_unsigned(3)
        maximum_decrease = -(1 << (block_value_bits - 1))
        maximum_increase = (1 << (block_value_bits - 1)) - 1
        field_width = 0
        while len(decoded) < count:
            while True:
                delta = reader.read_signed(block_value_bits)
                field_width += delta
                if delta not in (maximum_decrease, maximum_increase):
                    break
            run_length = reader.read_unsigned(block_length_bits)
            if run_length <= 0:
                raise ValueError("bitlength run length must advance the output")
            for _index in range(run_length):
                decoded.append(reader.read_signed(field_width) + mean)

    if len(decoded) != count or reader.position != bit_count:
        raise ValueError(
            "Int32CDP2 bitlength mismatch: decoded %d/%d symbols, consumed %d/%d bits"
            % (len(decoded), count, reader.position, bit_count)
        )
    return decoded


def self_check():
    """Check fixed-width and run-width packet decoding.

    > example
    > self_check() -> prints 'bitlength2 self-check ok' or asserts
    """
    assert decode_bitlength2(1, [0x020D0000], 16) == [0]
    variable_bits = "1" + "0" * 32 + "011" + "010" + "001" + "01" + "1"
    padded_bits = variable_bits.ljust(
        ((len(variable_bits) + 31) // 32) * 32, "0"
    )
    words = [
        int(padded_bits[offset:offset + 32], 2)
        for offset in range(0, len(padded_bits), 32)
    ]
    assert decode_bitlength2(1, words, len(variable_bits)) == [-1]
    print("bitlength2 self-check ok")


if __name__ == "__main__":
    self_check()
