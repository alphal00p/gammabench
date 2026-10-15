use std::{env, error::Error, fs, hint::black_box, time::Instant};

fn main() -> Result<(), Box<dyn Error>> {
    let mut arguments = env::args().skip(1);
    let implementation = arguments.next().ok_or("expected stable or unstable")?;
    if implementation == "--version" {
        println!("gammabench-rust-sort 1");
        return Ok(());
    }
    let unstable = match implementation.as_str() {
        "stable" => false,
        "unstable" => true,
        _ => return Err("expected stable or unstable".into()),
    };
    if arguments.next().is_some() {
        return Err("unexpected argument".into());
    }
    let items: u64 = fs::read_to_string("workload.txt")?.trim().parse()?;
    if items == 0 || items > u32::MAX.into() {
        return Err("workload size must fit a positive u32".into());
    }
    let mut values: Vec<u64> = (0..items).collect();
    // A fixed Fisher-Yates permutation keeps both implementations on identical input.
    let mut state = 0x4d59_5df4_d0f3_3173_u64;
    for index in (1..values.len()).rev() {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        values.swap(index, (state % (index as u64 + 1)) as usize);
    }

    let started = Instant::now();
    if unstable {
        black_box(&mut values).sort_unstable();
    } else {
        black_box(&mut values).sort();
    }
    black_box(&values);
    let compute_ns = started.elapsed().as_nanos();

    // Check the exact output after the clock; counts or a checksum alone are insufficient.
    if !values.iter().copied().eq(0..items) {
        return Err("sorted output differs from the exact expected sequence".into());
    }
    let total: u64 = values.iter().sum();
    let sorted_fnv = values
        .iter()
        .flat_map(|value| value.to_le_bytes())
        .fold(0xcbf2_9ce4_8422_2325_u64, |hash, byte| {
            (hash ^ u64::from(byte)).wrapping_mul(0x100_0000_01b3)
        });
    println!(
        "RESULT {{\"compute_ns\":{compute_ns},\"items\":{items},\"total\":{total},\"sorted_fnv\":\"{sorted_fnv:016x}\"}}"
    );
    Ok(())
}
